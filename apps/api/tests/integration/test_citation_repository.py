"""Real-Postgres proof for citation persistence (issue #59, ADR-8.6) —
runs as app_api, the same role the running HTTP process uses.

The literal ADR-8.6 acceptance scenario: a citation survives, with its
provenance snapshot intact, after its source document (and
cascade-deleted chunk) is deleted via issue #48's real DELETE endpoint
— proving the FK is genuinely ON DELETE SET NULL, not CASCADE (which
would silently rewrite the historical answer) or RESTRICT (which would
block FR-KB-5's provable deletion).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from aether.adapters.postgres.citation_repository import PostgresCitationRepository
from aether.adapters.postgres.document_repository import PostgresDocumentRepository
from aether.domain.entities import CitationDraft

pytestmark = pytest.mark.integration


async def _seed_workspace_user_thread_message(
    bootstrap_pool: asyncpg.Pool,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (workspace_id, message_id) — the minimal real chain a
    citation FKs into (message_citations.message_id -> messages.id,
    messages.thread_id -> threads.id, threads.created_by -> users.id)."""
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Citation Test', $2)",
            workspace_id,
            f"citation-test-{workspace_id}",
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
        await conn.execute(
            "INSERT INTO messages (id, workspace_id, thread_id, seq, role, content, status, "
            "grounded) VALUES ($1, $2, $3, 1, 'assistant', 'Acme costs $10/mo.', 'complete', true)",
            message_id,
            workspace_id,
            thread_id,
        )
    return workspace_id, message_id


async def _seed_ready_document_with_chunk(
    bootstrap_pool: asyncpg.Pool, workspace_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key, status) VALUES "
            "($1, $2, 'pricing.md', 'x', 'text/markdown', 100, 'k', 'ready')",
            document_id,
            workspace_id,
        )
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count) VALUES "
            "($1, $2, $3, 'Pricing', 0, 20, 'Acme costs $10/mo.', 'h', 5)",
            chunk_id,
            workspace_id,
            document_id,
        )
    return document_id, chunk_id


async def test_citations_are_created_and_listed_by_message(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, message_id = await _seed_workspace_user_thread_message(db_bootstrap_pool)
    _, chunk_id = await _seed_ready_document_with_chunk(db_bootstrap_pool, workspace_id)

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        repo = PostgresCitationRepository(conn)
        created = await repo.create_many(
            workspace_id,
            message_id,
            [
                CitationDraft(
                    chunk_id=chunk_id,
                    document_title="pricing.md",
                    section_path="Pricing",
                    page_start=1,
                    page_end=1,
                )
            ],
        )
        listed = await repo.list_by_message(workspace_id, message_id)

    assert len(created) == 1
    assert created[0].chunk_id == chunk_id
    assert created[0].document_title == "pricing.md"
    assert [c.id for c in listed] == [c.id for c in created]


async def test_create_many_with_no_citations_is_a_clean_no_op(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, message_id = await _seed_workspace_user_thread_message(db_bootstrap_pool)

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        repo = PostgresCitationRepository(conn)
        created = await repo.create_many(workspace_id, message_id, [])

    assert created == []


async def test_a_citation_survives_with_its_snapshot_intact_after_its_source_is_deleted(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """The literal ADR-8.6 acceptance scenario."""
    workspace_id, message_id = await _seed_workspace_user_thread_message(db_bootstrap_pool)
    document_id, chunk_id = await _seed_ready_document_with_chunk(db_bootstrap_pool, workspace_id)

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        citation_repo = PostgresCitationRepository(conn)
        [citation] = await citation_repo.create_many(
            workspace_id,
            message_id,
            [
                CitationDraft(
                    chunk_id=chunk_id,
                    document_title="pricing.md",
                    section_path="Pricing",
                    page_start=1,
                    page_end=1,
                )
            ],
        )

    # Delete the document via the real API-plane repository (issue #48)
    # — hard-deletes the chunk in the same transaction, exactly like a
    # real DELETE /documents/{id} request.
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        deleted = await PostgresDocumentRepository(conn).delete(
            workspace_id, document_id, deleted_at=datetime.now(UTC)
        )
    assert deleted is True

    async with db_bootstrap_pool.acquire() as conn:
        chunk_gone = await conn.fetchval("SELECT id FROM chunks WHERE id = $1", chunk_id)
        citation_row = await conn.fetchrow(
            "SELECT chunk_id, document_title, section_path, page_start, page_end "
            "FROM message_citations WHERE id = $1",
            citation.id,
        )

    assert chunk_gone is None  # the chunk itself is really gone (FR-KB-5)
    assert citation_row is not None  # the citation row survives
    assert citation_row["chunk_id"] is None  # nulled, not left dangling
    # ...but the provenance snapshot is untouched — this is the whole
    # point of denormalizing it at write time rather than joining live.
    assert citation_row["document_title"] == "pricing.md"
    assert citation_row["section_path"] == "Pricing"
    assert citation_row["page_start"] == 1
    assert citation_row["page_end"] == 1


async def test_citations_cannot_be_read_across_workspaces(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_a, message_a = await _seed_workspace_user_thread_message(db_bootstrap_pool)
    workspace_b, _ = await _seed_workspace_user_thread_message(db_bootstrap_pool)
    _, chunk_id = await _seed_ready_document_with_chunk(db_bootstrap_pool, workspace_a)

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_a))
        await PostgresCitationRepository(conn).create_many(
            workspace_a,
            message_a,
            [
                CitationDraft(
                    chunk_id=chunk_id,
                    document_title="pricing.md",
                    section_path="Pricing",
                    page_start=None,
                    page_end=None,
                )
            ],
        )

    # RLS, not just the query's own explicit WHERE clause, is what must
    # block this: the connection's tenant context is workspace_b, but
    # the call itself claims the *correct* (workspace_a, message_a) —
    # proving RLS enforces regardless of what the caller's arguments say.
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_b))
        cross_tenant_view = await PostgresCitationRepository(conn).list_by_message(
            workspace_a, message_a
        )

    assert cross_tenant_view == []
