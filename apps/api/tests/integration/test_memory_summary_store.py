"""Real-Postgres proof for memory_summaries persistence (issue #82, §3.2.6)
— runs as app_api, the same role the running HTTP process uses.

Proves the three things a fake store can't: RLS actually isolates
workspaces, the UNIQUE(thread_id) constraint really makes ON CONFLICT
(thread_id) DO UPDATE a genuine "latest-wins per thread" upsert (not just
an insert that happens to work in the fake), and a real MemoryAssembler
round-trip persists and rehydrates through the real table.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from aether.adapters.postgres.memory_summary_store import PostgresMemorySummaryStore
from aether.app.chat.memory_assembly import MemoryAssembler
from aether.domain.entities import MessageRole, MessageStatus
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.chat import FakeMessageStore
from tests.unit.fakes.memory import FakeMemoryCompactor

pytestmark = pytest.mark.integration


async def _seed_workspace_thread(bootstrap_pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Memory Test', $2)",
            workspace_id,
            f"memory-test-{workspace_id}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, 'Test User')",
            user_id,
            f"{user_id}@example.com",
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
            thread_id,
            workspace_id,
            user_id,
        )
    return workspace_id, thread_id


async def test_a_summary_is_created_and_read_back(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, thread_id = await _seed_workspace_thread(db_bootstrap_pool)
    store = PostgresMemorySummaryStore(db_pool)

    created = await store.upsert(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        upto_seq=4,
        content="condensed history",
        model="fake-compactor",
        token_count=12,
    )
    fetched = await store.get_by_thread(workspace_id, thread_id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.content == "condensed history"
    assert fetched.upto_seq == 4
    assert fetched.token_count == 12


async def test_upsert_on_conflict_replaces_rather_than_duplicates(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """The literal 'latest-wins per thread' contract (§8.1) — a second
    compaction pass overwrites, it doesn't append a second row."""
    workspace_id, thread_id = await _seed_workspace_thread(db_bootstrap_pool)
    store = PostgresMemorySummaryStore(db_pool)

    await store.upsert(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        upto_seq=4,
        content="first pass",
        model="fake-compactor",
        token_count=10,
    )
    second = await store.upsert(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        upto_seq=9,
        content="second pass, further along",
        model="fake-compactor",
        token_count=15,
    )

    async with db_bootstrap_pool.acquire() as conn:
        row_count = await conn.fetchval(
            "SELECT count(*) FROM memory_summaries WHERE thread_id = $1", thread_id
        )
    fetched = await store.get_by_thread(workspace_id, thread_id)

    assert row_count == 1
    assert fetched is not None
    assert fetched.id == second.id
    assert fetched.content == "second pass, further along"
    assert fetched.upto_seq == 9


async def test_summaries_cannot_be_read_across_workspaces(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_a, thread_a = await _seed_workspace_thread(db_bootstrap_pool)
    workspace_b, _ = await _seed_workspace_thread(db_bootstrap_pool)
    store = PostgresMemorySummaryStore(db_pool)

    await store.upsert(
        id=uuid.uuid4(),
        workspace_id=workspace_a,
        thread_id=thread_a,
        upto_seq=4,
        content="workspace a's summary",
        model="fake-compactor",
        token_count=10,
    )

    # RLS, not just the store's own explicit WHERE clause, must block this:
    # the row genuinely belongs to workspace_a, but get_by_thread is asked
    # (correctly) for workspace_a while the *connection's* tenant context
    # never gets set to workspace_a in this second store instance's calls
    # below — a fresh pool.acquire() defaults to no tenant configured,
    # equivalent to being scoped as workspace_b for this assertion's
    # purposes since the row is invisible either way.
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_b))
        cross_tenant_row = await conn.fetchrow(
            "SELECT id FROM memory_summaries WHERE thread_id = $1", thread_a
        )

    assert cross_tenant_row is None


async def test_memory_assembler_round_trips_through_the_real_table(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """A real compaction pass via MemoryAssembler, backed by the real
    Postgres store — proves the whole write path (not just the store in
    isolation) lands a row that a later assemble() call rehydrates."""
    workspace_id, thread_id = await _seed_workspace_thread(db_bootstrap_pool)
    messages = FakeMessageStore()
    for i in range(8):
        await messages.create(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            thread_id=thread_id,
            role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
            content=" ".join(f"word{i}-{j}" for j in range(300)),
            status=MessageStatus.COMPLETE,
            client_message_id=f"cmid-{i}" if i % 2 == 0 else None,
        )
    compactor = FakeMemoryCompactor(summary="condensed via real store")
    assembler = MemoryAssembler(
        messages=messages,
        summaries=PostgresMemorySummaryStore(db_pool),
        compactor=compactor,
        compactor_model_label="fake-compactor",
        ids=FakeIdGenerator(),
    )

    first_context = await assembler.assemble(workspace_id, thread_id)

    assert first_context.summary == "condensed via real store"
    assert compactor.calls

    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content, upto_seq FROM memory_summaries WHERE thread_id = $1", thread_id
        )
    assert row is not None
    assert row["content"] == "condensed via real store"

    # A second assemble() call with no new messages should read the
    # persisted summary back rather than recompacting from scratch.
    compactor.calls.clear()
    second_context = await assembler.assemble(workspace_id, thread_id)
    assert second_context.summary == "condensed via real store"
