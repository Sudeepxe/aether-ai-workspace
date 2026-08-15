"""RLS canary for threads/messages (Sprint 3, issue #24) — the same
falsifying-test discipline as test_rls_canary.py/test_tenancy_and_audit_rls.py:
every assertion runs against app_api, never a superuser.

Also covers issue #24's literal acceptance criterion: a concurrency test
proving no seq collisions under simultaneous inserts to the same thread
(ADR-8.2's locked per-thread counter row).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from aether.adapters.postgres.message_repository import PostgresMessageRepository
from aether.adapters.postgres.thread_repository import PostgresThreadRepository
from aether.domain.entities import MessageRole, MessageStatus

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


async def test_tenant_cannot_read_another_tenants_threads(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        thread_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
            thread_id,
            ids["tenant_b"],
            ids["user_b"],
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        row = await conn.fetchrow("SELECT id FROM threads WHERE id = $1", thread_id)
        assert row is None, "tenant A must not see tenant B's thread"

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        row = await conn.fetchrow("SELECT id FROM threads WHERE id = $1", thread_id)
        assert row is not None, "tenant B must see its own thread"


async def test_tenant_cannot_write_a_thread_under_another_tenants_id(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
                uuid.uuid4(),
                ids["tenant_b"],  # mismatched tenant — WITH CHECK must refuse
                ids["user_a"],
            )


async def test_tenant_cannot_read_another_tenants_messages(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        thread_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
            thread_id,
            ids["tenant_b"],
            ids["user_b"],
        )
        message_repo = PostgresMessageRepository(conn)
        message = await message_repo.create(
            id=uuid.uuid4(),
            workspace_id=ids["tenant_b"],
            thread_id=thread_id,
            role=MessageRole.USER,
            content="secret",
            status=MessageStatus.COMPLETE,
            client_message_id="cmid",
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        row = await conn.fetchrow("SELECT id FROM messages WHERE id = $1", message.id)
        assert row is None, "tenant A must not see tenant B's message"


async def test_concurrent_message_inserts_to_the_same_thread_never_collide_on_seq(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """ADR-8.2's exact concern: a naive max(seq)+1 races under concurrent
    inserts to the same thread. Fire many concurrent creates at the real
    locked-counter-row implementation and prove every seq is unique and
    the set is gapless 1..N — proof, not assertion, that the FOR UPDATE
    lock is doing its job."""
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        thread_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
            thread_id,
            ids["tenant_a"],
            ids["user_a"],
        )

    concurrency = 20

    async def _insert_one(i: int) -> int:
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
            message = await PostgresMessageRepository(conn).create(
                id=uuid.uuid4(),
                workspace_id=ids["tenant_a"],
                thread_id=thread_id,
                role=MessageRole.USER,
                content=f"message {i}",
                status=MessageStatus.COMPLETE,
                client_message_id=f"cmid-{i}",
            )
            return message.seq

    seqs = await asyncio.gather(*[_insert_one(i) for i in range(concurrency)])

    assert len(set(seqs)) == concurrency, f"seq collision detected: {sorted(seqs)}"
    assert sorted(seqs) == list(range(1, concurrency + 1)), "seq must be gapless 1..N"


async def test_thread_soft_delete_scoped_to_workspace(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """The app-layer typed-parameter discipline, proven at the repository
    level: a delete scoped to the wrong workspace_id must be a no-op, not
    a cross-tenant deletion (defense in depth even though RLS alone would
    already prevent the UPDATE from matching any row)."""
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        thread_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
            thread_id,
            ids["tenant_b"],
            ids["user_b"],
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        thread_repo = PostgresThreadRepository(conn)
        await thread_repo.soft_delete(ids["tenant_a"], thread_id, deleted_at=datetime.now(UTC))

    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT deleted_at FROM threads WHERE id = $1", thread_id)
        assert row is not None
        assert row["deleted_at"] is None, "cross-workspace delete attempt must be a no-op"
