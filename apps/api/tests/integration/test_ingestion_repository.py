"""Real-Postgres proof for PostgresIngestionRepository (issue #46) —
runs as app_worker, the same least-privileged role the running worker
process uses, not a superuser.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.domain.entities import ChunkDraft, DocumentStatus

pytestmark = pytest.mark.integration


async def _seed_document(bootstrap_pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    workspace_id, document_id = uuid.uuid4(), uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Ingestion Test', $2)",
            workspace_id,
            f"ingestion-test-{workspace_id}",
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            document_id,
            workspace_id,
        )
    return workspace_id, document_id


async def test_update_status_advances_the_document(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)

    await repo.update_status(workspace_id, document_id, status=DocumentStatus.SCANNING)

    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM documents WHERE id = $1", document_id)
    assert row["status"] == "scanning"


async def test_mark_failed_records_stage_and_reason(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)

    await repo.mark_failed(workspace_id, document_id, stage="parsing", reason="encrypted PDF")

    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, failure_stage, failure_reason FROM documents WHERE id = $1",
            document_id,
        )
    assert row["status"] == "failed"
    assert row["failure_stage"] == "parsing"
    assert row["failure_reason"] == "encrypted PDF"


async def test_insert_chunks_and_advance_is_one_atomic_transaction(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)
    drafts = [
        ChunkDraft(
            content=f"chunk {i}",
            section_path="Title",
            page_start=None,
            page_end=None,
            char_start=i * 10,
            char_end=i * 10 + 5,
            token_count=2,
        )
        for i in range(3)
    ]

    await repo.insert_chunks_and_advance(
        workspace_id, document_id, chunks=drafts, next_status=DocumentStatus.EMBEDDING
    )

    async with db_bootstrap_pool.acquire() as conn:
        doc_row = await conn.fetchrow("SELECT status FROM documents WHERE id = $1", document_id)
        chunk_rows = await conn.fetch(
            "SELECT content, embedding FROM chunks WHERE document_id = $1 ORDER BY char_start",
            document_id,
        )
    assert doc_row["status"] == "embedding"
    assert [r["content"] for r in chunk_rows] == ["chunk 0", "chunk 1", "chunk 2"]
    assert all(r["embedding"] is None for r in chunk_rows)  # issue #47's job, not this one


async def test_insert_chunks_and_advance_is_idempotent_under_redelivery(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """A redelivered ingestion message re-running the same chunking
    result must not duplicate rows or fail with a unique-violation that
    would otherwise wedge the document permanently in CHUNKING."""
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)
    drafts = [
        ChunkDraft(
            content="only chunk",
            section_path="Title",
            page_start=None,
            page_end=None,
            char_start=0,
            char_end=10,
            token_count=2,
        )
    ]

    await repo.insert_chunks_and_advance(
        workspace_id, document_id, chunks=drafts, next_status=DocumentStatus.EMBEDDING
    )
    await repo.insert_chunks_and_advance(  # simulated redelivery of the same message
        workspace_id, document_id, chunks=drafts, next_status=DocumentStatus.EMBEDDING
    )

    async with db_bootstrap_pool.acquire() as conn:
        chunk_rows = await conn.fetch("SELECT id FROM chunks WHERE document_id = $1", document_id)
    assert len(chunk_rows) == 1
