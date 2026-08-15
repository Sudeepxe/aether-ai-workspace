"""Migrations apply cleanly from an empty database (Sprint 1 DoD)."""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.integration


async def test_expected_tables_exist(db_bootstrap_pool: asyncpg.Pool) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    tables = {r["tablename"] for r in rows}
    assert {
        "users",
        "identities",
        "workspaces",
        "memberships",
        "refresh_tokens",
        "invitations",
        "audit_events",
        "audit_events_2026_08",
        "audit_events_2026_09",
        "threads",
        "messages",
        "thread_seq_counters",
        "usage_events",
        "usage_events_2026_08",
        "usage_events_2026_09",
        "budgets",
        "global_usage_counter",
    } <= tables


async def test_expected_roles_exist_with_correct_bypassrls(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname LIKE 'app_%'"
        )
    roles = {r["rolname"]: r["rolbypassrls"] for r in rows}
    assert roles == {"app_api": False, "app_worker": False, "app_migrator": True}


@pytest.mark.parametrize(
    "table", ["memberships", "audit_events", "threads", "messages", "usage_events", "budgets"]
)
async def test_tenant_scoped_tables_have_rls_enabled_and_forced(
    db_bootstrap_pool: asyncpg.Pool, table: str
) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1", table
        )
    assert row["relrowsecurity"] is True
    assert row["relforcerowsecurity"] is True


async def test_invitations_is_deliberately_rls_exempt(db_bootstrap_pool: asyncpg.Pool) -> None:
    """invitations is RLS-exempt by design (see the migration's own
    comment): the accept-by-token flow has no tenant context to set. This
    guards against RLS being silently re-added without updating the
    repository's explicit workspace_id-scoping discipline to match."""
    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'invitations'"
        )
    assert row["relrowsecurity"] is False


async def test_app_api_has_no_update_or_delete_grant_on_audit_events(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'audit_events' AND grantee = 'app_api'"
        )
    privileges = {r["privilege_type"] for r in rows}
    assert privileges == {"SELECT", "INSERT"}, (
        f"audit_events must be INSERT-only for app_api (§3.7.3 tamper resistance); got {privileges}"
    )


async def test_app_api_has_no_update_or_delete_grant_on_usage_events(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'usage_events' AND grantee = 'app_api'"
        )
    privileges = {r["privilege_type"] for r in rows}
    assert privileges == {"SELECT", "INSERT"}, (
        f"usage_events must be INSERT-only for app_api — a ledger app code can rewrite "
        f"isn't a ledger; got {privileges}"
    )


async def test_app_worker_can_settle_budgets_but_not_insert_or_delete(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    """The metering consumer (F-4: batched settlement) needs UPDATE on
    budgets to write settled_microcents, but must never be able to
    create or remove a workspace's budget row — that's an app_api-owned
    lifecycle concern (workspace creation / the budget PUT endpoint)."""
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'budgets' AND grantee = 'app_worker'"
        )
    privileges = {r["privilege_type"] for r in rows}
    assert privileges == {"SELECT", "UPDATE"}, f"expected SELECT+UPDATE only; got {privileges}"


async def test_global_usage_counter_is_deliberately_rls_exempt(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    """Not tenant data — it's the single cross-workspace aggregate the
    global kill switch (NFR-C-1) reads, which budgets' forced per-tenant
    RLS cannot answer under a per-request tenant context (see this
    table's own migration comment for the real bug this fixed)."""
    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'global_usage_counter'"
        )
    assert row["relrowsecurity"] is False


async def test_global_usage_counter_is_seeded_with_exactly_one_row(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, settled_microcents FROM global_usage_counter")
    assert len(rows) == 1
    assert rows[0]["id"] is True
    assert rows[0]["settled_microcents"] == 0


async def test_app_api_has_no_insert_or_delete_grant_on_global_usage_counter(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    """A CHECK(id) singleton table: app_api must never be able to INSERT
    a second row or DELETE the only one — only SELECT/UPDATE its value."""
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'global_usage_counter' AND grantee = 'app_api'"
        )
    privileges = {r["privilege_type"] for r in rows}
    assert privileges == {"SELECT", "UPDATE"}, f"expected SELECT+UPDATE only; got {privileges}"
