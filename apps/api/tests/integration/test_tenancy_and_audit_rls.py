"""RLS canary for Sprint 2's new tenant-scoped tables (invitations,
audit_events) — the same falsifying-test discipline as Sprint 1's
test_rls_canary.py: every assertion runs against app_api (never a
superuser, never with application-code filtering), and if any of these
fail, RLS/grants are not doing their job.

audit_events gets two additional properties beyond plain tenant
isolation, both load-bearing for FR-AD-1 / §3.7.3's "audit tampering"
threat row:

- INSERT-only at the grant level: UPDATE/DELETE must fail with a
  privilege error, not merely be blocked by RLS (a privilege failure
  means tampering is impossible even for a role that somehow acquired a
  matching tenant context; an RLS failure alone would not).
- workspace_id is nullable for system/auth-plane events (§8.1) — a
  session with no tenant context set (the auth-plane case: login has no
  workspace yet) must be able to write and read only NULL-workspace
  rows, and a tenant-context session must never see them.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def _seed_two_tenants(bootstrap_pool: asyncpg.Pool) -> dict[str, uuid.UUID]:
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
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
    return {"tenant_a": tenant_a, "tenant_b": tenant_b, "user_a": user_a, "user_b": user_b}


# --------------------------------------------------------------- invitations


async def _seed_invitation(
    bootstrap_pool: asyncpg.Pool, *, workspace_id: uuid.UUID, invited_by: uuid.UUID
) -> uuid.UUID:
    invitation_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO invitations (id, workspace_id, email, role, token_hash, invited_by, expires_at) "
            "VALUES ($1, $2, 'invitee@example.com', 'member', $3, $4, now() + interval '7 days')",
            invitation_id,
            workspace_id,
            uuid.uuid4().hex,
            invited_by,
        )
    return invitation_id


async def test_tenant_cannot_read_other_tenants_invitation(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    await _seed_invitation(
        db_bootstrap_pool, workspace_id=ids["tenant_b"], invited_by=ids["user_b"]
    )
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        rows = await conn.fetch("SELECT id FROM invitations")
    assert rows == []


async def test_tenant_cannot_consume_other_tenants_invitation(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    invitation_b = await _seed_invitation(
        db_bootstrap_pool, workspace_id=ids["tenant_b"], invited_by=ids["user_b"]
    )
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        result = await conn.execute(
            "UPDATE invitations SET consumed_at = now() WHERE id = $1", invitation_b
        )
    assert result == "UPDATE 0"


async def test_missing_tenant_context_blocks_invitation_insert(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    with pytest.raises(asyncpg.exceptions.PostgresError):
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO invitations (id, workspace_id, email, role, token_hash, invited_by, expires_at) "
                "VALUES ($1, $2, 'x@example.com', 'member', $3, $4, now() + interval '7 days')",
                uuid.uuid4(),
                ids["tenant_a"],
                uuid.uuid4().hex,
                ids["user_a"],
            )


# --------------------------------------------------------------- audit_events


async def test_tenant_cannot_read_other_tenants_audit_events(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_events (id, workspace_id, actor_user_id, action, target_type, target_id) "
            "VALUES ($1, $2, $3, 'membership.role_changed', 'membership', $4)",
            uuid.uuid4(),
            ids["tenant_b"],
            ids["user_b"],
            uuid.uuid4(),
        )
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        rows = await conn.fetch("SELECT id FROM audit_events")
    assert rows == []


async def test_app_api_cannot_update_audit_events(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    event_id = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_events (id, workspace_id, actor_user_id, action, target_type, target_id) "
            "VALUES ($1, $2, $3, 'membership.role_changed', 'membership', $4)",
            event_id,
            ids["tenant_a"],
            ids["user_a"],
            uuid.uuid4(),
        )
    # A privilege error, not an RLS-shaped "0 rows affected" — proves
    # tampering is impossible at the grant level, independent of whether
    # the tenant context happens to match.
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            await conn.execute(
                "UPDATE audit_events SET action = 'tampered' WHERE id = $1", event_id
            )


async def test_app_api_cannot_delete_audit_events(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    event_id = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_events (id, workspace_id, actor_user_id, action, target_type, target_id) "
            "VALUES ($1, $2, $3, 'membership.role_changed', 'membership', $4)",
            event_id,
            ids["tenant_a"],
            ids["user_a"],
            uuid.uuid4(),
        )
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            await conn.execute("DELETE FROM audit_events WHERE id = $1", event_id)


async def test_context_free_session_can_write_and_read_only_system_level_events(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    # A tenant-scoped event, seeded independently of the session under test.
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_events (id, workspace_id, actor_user_id, action, target_type, target_id) "
            "VALUES ($1, $2, $3, 'membership.role_changed', 'membership', $4)",
            uuid.uuid4(),
            ids["tenant_a"],
            ids["user_a"],
            uuid.uuid4(),
        )

    # No SET LOCAL app.tenant_id at all — the auth-plane case (e.g. login,
    # which precedes any workspace selection).
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO audit_events (id, actor_user_id, action, target_type, target_id) "
            "VALUES ($1, $2, 'auth.login_succeeded', 'user', $2)",
            uuid.uuid4(),
            ids["user_a"],
        )
        rows = await conn.fetch("SELECT id, workspace_id, action FROM audit_events")

    assert len(rows) == 1
    assert rows[0]["workspace_id"] is None
    assert rows[0]["action"] == "auth.login_succeeded"


async def test_tenant_context_session_cannot_write_system_level_event(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    with pytest.raises(asyncpg.exceptions.PostgresError):
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            # workspace_id omitted (NULL) while a tenant context IS set —
            # WITH CHECK must reject this: NULL is distinct from a real
            # tenant UUID, so this can never be this tenant's own write.
            await conn.execute(
                "INSERT INTO audit_events (id, actor_user_id, action, target_type, target_id) "
                "VALUES ($1, $2, 'auth.login_succeeded', 'user', $2)",
                uuid.uuid4(),
                ids["user_a"],
            )
