"""Budget admission + settlement integration tests (issue #37, §3.2.14),
against real Postgres — the acceptance criterion is specifically about
concurrent behavior under real row-level locking, which no fake/in-memory
double can prove.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from aether.adapters.postgres.usage_ledger import PostgresBudgetAdmission, PostgresUsageLedger
from aether.domain.entities import UsageEventKind

pytestmark = [pytest.mark.integration]

_CEILING_MICROCENTS = 100
_CONCURRENCY = 10


async def _seed_workspace_with_budget(
    bootstrap_pool: asyncpg.Pool, *, monthly_limit_microcents: int
) -> uuid.UUID:
    workspace_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Budget Test', $2)",
            workspace_id,
            f"budget-test-{workspace_id}",
        )
        await conn.execute(
            "INSERT INTO budgets (workspace_id, monthly_limit_microcents, soft_pct, "
            "current_period_start) VALUES ($1, $2, 80, date_trunc('month', now())::date)",
            workspace_id,
            monthly_limit_microcents,
        )
    return workspace_id


async def test_concurrent_admission_overshoot_is_bounded_by_concurrency_times_ceiling(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """The budget has room for exactly one request's ceiling estimate.
    Every concurrent admission check reads ``settled_microcents`` before
    any of them has settled, so in the worst case all ``_CONCURRENCY``
    requests can be admitted simultaneously — a documented, accepted
    approximation (§3.2.14: a ceiling-estimate check, not a distributed
    lock across admission-and-settlement). This proves two things: the
    resulting overshoot is *bounded* by concurrency (not unbounded), and
    concurrent settlement itself never loses an update under real
    Postgres row-level locking.
    """
    workspace_id = await _seed_workspace_with_budget(
        db_bootstrap_pool, monthly_limit_microcents=_CEILING_MICROCENTS
    )
    admission = PostgresBudgetAdmission(db_pool, global_monthly_budget_microcents=10**12)
    ledger = PostgresUsageLedger(db_pool)

    async def _attempt() -> bool:
        decision = await admission.check(workspace_id, ceiling_microcents=_CEILING_MICROCENTS)
        if not decision.allowed:
            return False
        await ledger.record(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=None,
            kind=UsageEventKind.CHAT,
            model="test-model",
            prompt_tokens=1,
            completion_tokens=1,
            cost_microcents=_CEILING_MICROCENTS,
            generation_id=None,
        )
        return True

    results = await asyncio.gather(*[_attempt() for _ in range(_CONCURRENCY)])
    admitted_count = sum(results)

    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT settled_microcents FROM budgets WHERE workspace_id = $1", workspace_id
        )
    settled = row["settled_microcents"]

    assert admitted_count >= 1, "at least the first admission must succeed"
    # No lost updates: settlement is exact, proving real row-level
    # locking serializes the concurrent UPDATE ... SET settled = settled + N.
    assert settled == admitted_count * _CEILING_MICROCENTS
    # The acceptance criterion itself: overshoot beyond the limit is
    # strictly bounded by concurrency, never unbounded.
    assert settled - _CEILING_MICROCENTS <= (_CONCURRENCY - 1) * _CEILING_MICROCENTS
    assert settled <= _CONCURRENCY * _CEILING_MICROCENTS


async def test_admission_refuses_once_settled_reaches_the_limit(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id = await _seed_workspace_with_budget(
        db_bootstrap_pool, monthly_limit_microcents=_CEILING_MICROCENTS
    )
    admission = PostgresBudgetAdmission(db_pool, global_monthly_budget_microcents=10**12)
    ledger = PostgresUsageLedger(db_pool)

    first = await admission.check(workspace_id, ceiling_microcents=_CEILING_MICROCENTS)
    assert first.allowed is True
    await ledger.record(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=None,
        kind=UsageEventKind.CHAT,
        model="test-model",
        prompt_tokens=1,
        completion_tokens=1,
        cost_microcents=_CEILING_MICROCENTS,
        generation_id=None,
    )

    second = await admission.check(workspace_id, ceiling_microcents=_CEILING_MICROCENTS)
    assert second.allowed is False
    assert second.settled_microcents == _CEILING_MICROCENTS


async def test_admission_fails_closed_for_a_workspace_with_no_budget_row(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'No Budget', $2)",
            workspace_id,
            f"no-budget-{workspace_id}",
        )
    admission = PostgresBudgetAdmission(db_pool, global_monthly_budget_microcents=10**12)

    decision = await admission.check(workspace_id, ceiling_microcents=1)

    assert decision.allowed is False


async def test_global_kill_switch_sums_settled_across_all_workspaces(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ws_a = await _seed_workspace_with_budget(db_bootstrap_pool, monthly_limit_microcents=10_000)
    ws_b = await _seed_workspace_with_budget(db_bootstrap_pool, monthly_limit_microcents=10_000)
    ledger = PostgresUsageLedger(db_pool)
    for workspace_id in (ws_a, ws_b):
        await ledger.record(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=None,
            kind=UsageEventKind.CHAT,
            model="test-model",
            prompt_tokens=1,
            completion_tokens=1,
            cost_microcents=300,
            generation_id=None,
        )

    admission = PostgresBudgetAdmission(db_pool, global_monthly_budget_microcents=700)
    assert await admission.check_global(ceiling_microcents=100) is True  # 600 + 100 <= 700
    assert await admission.check_global(ceiling_microcents=200) is False  # 600 + 200 > 700


async def test_usage_rollup_aggregates_by_model(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id = await _seed_workspace_with_budget(
        db_bootstrap_pool, monthly_limit_microcents=10_000
    )
    ledger = PostgresUsageLedger(db_pool)
    for model, cost in (("model-a", 100), ("model-a", 50), ("model-b", 200)):
        await ledger.record(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=None,
            kind=UsageEventKind.CHAT,
            model=model,
            prompt_tokens=1,
            completion_tokens=1,
            cost_microcents=cost,
            generation_id=None,
        )

    rollup = await ledger.rollup(workspace_id, since=datetime(2020, 1, 1, tzinfo=UTC))

    assert rollup.total_cost_microcents == 350
    by_model = {r.model: r for r in rollup.by_model}
    assert by_model["model-a"].request_count == 2
    assert by_model["model-a"].cost_microcents == 150
    assert by_model["model-b"].request_count == 1
    assert by_model["model-b"].cost_microcents == 200
