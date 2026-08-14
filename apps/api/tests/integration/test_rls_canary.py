"""RLS canary — the falsifying test for ADR-8.1's tenant-isolation claim.

Proves, against a real Postgres connected as the least-privileged app_api
role (never as a superuser, never with application-code filtering), that:

- Tenant A cannot read Tenant B's rows.
- Tenant A cannot update Tenant B's rows.
- Tenant A cannot delete Tenant B's rows.
- Missing/absent tenant context fails safe (zero rows), not open.

Fixture data is seeded via the bootstrap (RLS-bypassing) role — seeding is
test setup, not the app_api behavior under test. Every assertion below
runs against app_api, the same role the running application connects as.

If any of these fail, RLS is not doing its job and the test must fail —
this file must never be "fixed" by filtering in application code instead.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def _seed_two_tenants(bootstrap_pool: asyncpg.Pool) -> dict[str, uuid.UUID]:
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    membership_a, membership_b = uuid.uuid4(), uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, 'A'), ($3, $4, 'B')",
            user_a,
            f"{user_a}@example.com",
            user_b,
            f"{user_b}@example.com",
        )
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Tenant A', $2), ($3, 'Tenant B', $4)",
            tenant_a,
            f"tenant-a-{tenant_a}",
            tenant_b,
            f"tenant-b-{tenant_b}",
        )
        await conn.execute(
            "INSERT INTO memberships (id, workspace_id, user_id, role) "
            "VALUES ($1, $2, $3, 'owner'), ($4, $5, $6, 'owner')",
            membership_a,
            tenant_a,
            user_a,
            membership_b,
            tenant_b,
            user_b,
        )
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "membership_b": membership_b,
        "user_a": user_a,
    }


async def test_tenant_cannot_read_other_tenants_rows(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # SET LOCAL doesn't accept bind parameters; set_config() does
            # and is transaction-local with its third argument true.
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            rows = await conn.fetch("SELECT id, workspace_id FROM memberships")
    assert len(rows) == 1
    assert rows[0]["workspace_id"] == ids["tenant_a"]


async def test_tenant_cannot_update_other_tenants_row(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # SET LOCAL doesn't accept bind parameters; set_config() does
            # and is transaction-local with its third argument true.
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            result = await conn.execute(
                "UPDATE memberships SET role = 'viewer' WHERE id = $1", ids["membership_b"]
            )
    assert result == "UPDATE 0"


async def test_tenant_cannot_delete_other_tenants_row(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # SET LOCAL doesn't accept bind parameters; set_config() does
            # and is transaction-local with its third argument true.
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            result = await conn.execute(
                "DELETE FROM memberships WHERE id = $1", ids["membership_b"]
            )
    assert result == "DELETE 0"

    # Verify via the bootstrap (RLS-bypassing) role that tenant B's row
    # genuinely survived — not just that app_api *reported* zero deleted.
    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM memberships WHERE id = $1", ids["membership_b"])
    assert row is not None


async def test_missing_tenant_context_returns_zero_rows(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    await _seed_two_tenants(db_bootstrap_pool)
    async with db_pool.acquire() as conn:
        # Deliberately no SET LOCAL app.tenant_id at all.
        rows = await conn.fetch("SELECT id FROM memberships")
    assert rows == []


async def test_missing_tenant_context_blocks_insert(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    fresh_user = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        # A fresh user, not yet a member of anything — users carries no RLS
        # policy (it isn't tenant-scoped), so seeding it is unaffected by
        # the app_api scenario under test.
        await conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, 'C')",
            fresh_user,
            f"{fresh_user}@example.com",
        )

    with pytest.raises(asyncpg.exceptions.PostgresError):
        async with db_pool.acquire() as conn, conn.transaction():
            # No tenant context set — the WITH CHECK clause must reject this,
            # even though the row would otherwise be perfectly valid.
            await conn.execute(
                "INSERT INTO memberships (id, workspace_id, user_id, role) "
                "VALUES ($1, $2, $3, 'member')",
                uuid.uuid4(),
                ids["tenant_a"],
                fresh_user,
            )
