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


@pytest.mark.parametrize("table", ["memberships", "audit_events"])
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
