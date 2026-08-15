"""Migrations apply cleanly from an empty database (Sprint 1 DoD)."""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.integration


async def test_expected_tables_exist(db_bootstrap_pool: asyncpg.Pool) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    tables = {r["tablename"] for r in rows}
    assert {"users", "identities", "workspaces", "memberships", "refresh_tokens"} <= tables


async def test_expected_roles_exist_with_correct_bypassrls(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname LIKE 'app_%'"
        )
    roles = {r["rolname"]: r["rolbypassrls"] for r in rows}
    assert roles == {"app_api": False, "app_worker": False, "app_migrator": True}


async def test_memberships_rls_is_enabled_and_forced(db_bootstrap_pool: asyncpg.Pool) -> None:
    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'memberships'"
        )
    assert row["relrowsecurity"] is True
    assert row["relforcerowsecurity"] is True
