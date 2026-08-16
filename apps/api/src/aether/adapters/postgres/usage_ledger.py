"""Pool-bound usage ledger + budget admission adapters (§3.2.14).

Both are pool-bound, not connection-bound — see ports/metering.py's
module docstring for why: the admission check runs on the orchestrator's
hot path (no long-lived connection, same reasoning as MessageStorePort),
and the ledger is written by the worker's settlement consumer, which has
no per-request connection to bind to at all.

Settlement scope note (§8's F-4 self-review finding): F-4 calls for the
metering consumer to *batch* settlements across multiple usage events
per workspace (accumulate, flush <= 5s) to avoid hot-row contention on
``budgets`` under burst. This implementation settles each usage event in
its own transaction instead — correct (each transaction atomically
combines the ledger insert and the budget increment, so no update is
ever lost) but not throughput-optimized for high concurrent burst on one
workspace's budget row. True cross-event batching is a tracked follow-up,
not a correctness gap: Postgres row-level locking makes concurrent
settlement transactions serialize safely, just not maximally efficiently.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.metering import (
    AdmissionDecision,
    UsageEvent,
    UsageEventKind,
    UsageModelRollup,
    UsageRollup,
)


class PostgresBudgetAdmission:
    def __init__(self, pool: asyncpg.Pool, *, global_monthly_budget_microcents: int) -> None:
        self._pool = pool
        self._global_monthly_budget_microcents = global_monthly_budget_microcents

    async def check(self, workspace_id: UUID, *, ceiling_microcents: int) -> AdmissionDecision:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            row = await conn.fetchrow(
                "SELECT monthly_limit_microcents, soft_pct, settled_microcents "
                "FROM budgets WHERE workspace_id = $1",
                workspace_id,
            )
        if row is None:
            # Fail closed: an unconfigured budget must never mean
            # unlimited spend. CreateWorkspace provisions a default
            # budget row, so this only fires for a workspace whose
            # provisioning is somehow incomplete.
            return AdmissionDecision(
                allowed=False,
                soft_limit_crossed=False,
                settled_microcents=0,
                monthly_limit_microcents=0,
            )
        limit = row["monthly_limit_microcents"]
        settled = row["settled_microcents"]
        projected = settled + ceiling_microcents
        soft_threshold = limit * row["soft_pct"] // 100
        return AdmissionDecision(
            allowed=projected <= limit,
            soft_limit_crossed=projected > soft_threshold,
            settled_microcents=settled,
            monthly_limit_microcents=limit,
        )

    async def check_global(self, *, ceiling_microcents: int) -> bool:
        # budgets is forced-RLS and tenant-scoped — app_api can never see
        # a cross-tenant SUM over it under a per-request tenant context.
        # global_usage_counter is a dedicated, non-tenant-scoped singleton
        # row instead (see this adapter's migration for why).
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT settled_microcents FROM global_usage_counter WHERE id = true"
            )
        return int(total) + ceiling_microcents <= self._global_monthly_budget_microcents


class PostgresUsageLedger:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        user_id: UUID | None,
        kind: UsageEventKind,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_microcents: int,
        generation_id: UUID | None,
    ) -> UsageEvent:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            row = await conn.fetchrow(
                """
                INSERT INTO usage_events (
                    id, workspace_id, user_id, kind, model, prompt_tokens,
                    completion_tokens, cost_microcents, generation_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id, workspace_id, user_id, kind, model, prompt_tokens,
                          completion_tokens, cost_microcents, generation_id, occurred_at
                """,
                id,
                workspace_id,
                user_id,
                kind.value,
                model,
                prompt_tokens,
                completion_tokens,
                cost_microcents,
                generation_id,
            )
            assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
            # Settlement, same transaction as the ledger insert (see
            # module docstring re: F-4's batching optimization being a
            # tracked follow-up, not implemented per-event here).
            await conn.execute(
                "UPDATE budgets SET settled_microcents = settled_microcents + $2, updated_at = now() "
                "WHERE workspace_id = $1",
                workspace_id,
                cost_microcents,
            )
            # Same transaction, third statement: keeps the non-tenant-
            # scoped global counter (check_global's data source) exactly
            # in sync with every per-workspace settlement, atomically.
            await conn.execute(
                "UPDATE global_usage_counter SET settled_microcents = settled_microcents + $1, "
                "updated_at = now() WHERE id = true",
                cost_microcents,
            )
        return _row_to_usage_event(row)

    async def rollup(self, workspace_id: UUID, *, since: datetime) -> UsageRollup:
        # set_config(..., true) is transaction-local: without an explicit
        # conn.transaction() wrapping both statements, each is its own
        # auto-committed implicit transaction, so the tenant setting
        # reverts before the SELECT runs and RLS silently returns zero
        # rows for every workspace (a real bug caught by
        # test_usage_rollup_aggregates_by_model — see check()/record()
        # above for the correct pattern this now matches).
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            rows = await conn.fetch(
                """
                SELECT model, count(*) AS request_count, sum(prompt_tokens) AS prompt_tokens,
                       sum(completion_tokens) AS completion_tokens, sum(cost_microcents) AS cost_microcents
                FROM usage_events
                WHERE workspace_id = $1 AND occurred_at >= $2
                GROUP BY model
                ORDER BY model
                """,
                workspace_id,
                since,
            )
        by_model = [
            UsageModelRollup(
                model=row["model"],
                request_count=row["request_count"],
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                cost_microcents=row["cost_microcents"],
            )
            for row in rows
        ]
        return UsageRollup(
            workspace_id=workspace_id,
            period_start=since,
            by_model=by_model,
            total_cost_microcents=sum(r.cost_microcents for r in by_model),
        )


def _row_to_usage_event(row: asyncpg.Record) -> UsageEvent:
    return UsageEvent(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        kind=UsageEventKind(row["kind"]),
        model=row["model"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        cost_microcents=row["cost_microcents"],
        generation_id=row["generation_id"],
        occurred_at=row["occurred_at"],
    )
