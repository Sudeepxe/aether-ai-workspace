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


async def test_mark_failed_writes_a_document_failed_outbox_event(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)

    await repo.mark_failed(workspace_id, document_id, stage="parsing", reason="encrypted PDF")

    async with db_bootstrap_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT event_type, tenant_id, payload FROM outbox WHERE aggregate_id = $1", document_id
        )
    assert row is not None
    assert row["event_type"] == "document.failed"
    assert row["tenant_id"] == workspace_id
    assert row["payload"] == {
        "document_id": str(document_id),
        "stage": "parsing",
        "reason": "encrypted PDF",
    }


async def test_mark_failed_does_not_duplicate_the_outbox_event_under_redelivery(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)

    await repo.mark_failed(workspace_id, document_id, stage="parsing", reason="encrypted PDF")
    await repo.mark_failed(  # simulated redelivery hitting the same permanent failure again
        workspace_id, document_id, stage="parsing", reason="encrypted PDF"
    )

    async with db_bootstrap_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM outbox WHERE aggregate_id = $1 AND event_type = 'document.failed'",
            document_id,
        )
    assert count == 1


async def _insert_one_chunk(
    repo: PostgresIngestionRepository, workspace_id: uuid.UUID, document_id: uuid.UUID, content: str
) -> None:
    draft = ChunkDraft(
        content=content,
        section_path="Title",
        page_start=None,
        page_end=None,
        char_start=0,
        char_end=len(content),
        token_count=2,
    )
    await repo.insert_chunks_and_advance(
        workspace_id, document_id, chunks=[draft], next_status=DocumentStatus.EMBEDDING
    )


async def test_attach_embeddings_and_advance_persists_vectors_and_flips_status_to_ready(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)
    await _insert_one_chunk(repo, workspace_id, document_id, "hello there")

    async with db_bootstrap_pool.acquire() as conn:
        chunk_id = await conn.fetchval("SELECT id FROM chunks WHERE document_id = $1", document_id)
    vector = [0.25] * 1536

    await repo.attach_embeddings_and_advance(
        workspace_id,
        document_id,
        chunk_embeddings=[(chunk_id, vector)],
        embedding_model="text-embedding-3-small",
        embedding_version=1,
        next_status=DocumentStatus.READY,
    )

    async with db_bootstrap_pool.acquire() as conn:
        doc_row = await conn.fetchrow("SELECT status FROM documents WHERE id = $1", document_id)
        chunk_row = await conn.fetchrow(
            "SELECT embedding, embedding_model, embedding_version FROM chunks WHERE id = $1",
            chunk_id,
        )
        outbox_row = await conn.fetchrow(
            "SELECT event_type, payload FROM outbox WHERE aggregate_id = $1 "
            "AND event_type = 'document.ready'",
            document_id,
        )
    assert doc_row["status"] == "ready"
    assert chunk_row["embedding"].to_list() == pytest.approx(vector)
    assert chunk_row["embedding_model"] == "text-embedding-3-small"
    assert chunk_row["embedding_version"] == 1
    assert outbox_row is not None
    assert outbox_row["payload"]["document_id"] == str(document_id)


async def test_attach_embeddings_and_advance_does_not_duplicate_the_ready_event_under_redelivery(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)
    await _insert_one_chunk(repo, workspace_id, document_id, "hello there")
    async with db_bootstrap_pool.acquire() as conn:
        chunk_id = await conn.fetchval("SELECT id FROM chunks WHERE document_id = $1", document_id)
    vector = [0.25] * 1536

    for _ in range(2):  # simulated redelivery after a prior success
        await repo.attach_embeddings_and_advance(
            workspace_id,
            document_id,
            chunk_embeddings=[(chunk_id, vector)],
            embedding_model="text-embedding-3-small",
            embedding_version=1,
            next_status=DocumentStatus.READY,
        )

    async with db_bootstrap_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM outbox WHERE aggregate_id = $1 AND event_type = 'document.ready'",
            document_id,
        )
    assert count == 1


async def test_attach_embeddings_and_advance_is_atomic_a_mid_batch_failure_leaves_no_partial_rows(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """A batch mixing one valid embedding with one dimension-mismatched
    one (a real pgvector column-constraint violation) must roll back
    everything in the transaction — the earlier, otherwise-valid UPDATE
    included, and the document status flip to READY along with it."""
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)
    await _insert_one_chunk(repo, workspace_id, document_id, "chunk one")
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count) VALUES "
            "($1, $2, $3, 'Title', 20, 30, 'chunk two', 'h2', 2)",
            uuid.uuid4(),
            workspace_id,
            document_id,
        )
        chunk_rows = await conn.fetch(
            "SELECT id FROM chunks WHERE document_id = $1 ORDER BY char_start", document_id
        )
    good_id, bad_id = chunk_rows[0]["id"], chunk_rows[1]["id"]

    with pytest.raises(Exception):  # noqa: B017 - the exact asyncpg/pgvector error type isn't the point
        await repo.attach_embeddings_and_advance(
            workspace_id,
            document_id,
            chunk_embeddings=[(good_id, [0.1] * 1536), (bad_id, [0.1] * 4)],
            embedding_model="text-embedding-3-small",
            embedding_version=1,
            next_status=DocumentStatus.READY,
        )

    async with db_bootstrap_pool.acquire() as conn:
        doc_row = await conn.fetchrow("SELECT status FROM documents WHERE id = $1", document_id)
        chunk_rows = await conn.fetch(
            "SELECT embedding FROM chunks WHERE document_id = $1", document_id
        )
    assert doc_row["status"] == "embedding"  # never advanced to ready
    assert all(r["embedding"] is None for r in chunk_rows)  # the "good" update rolled back too


async def test_find_cached_embeddings_returns_hits_for_matching_content_hash(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, doc_a = await _seed_document(db_bootstrap_pool)
    _, doc_b = await _seed_document(db_bootstrap_pool)
    # doc_b belongs to a fresh random workspace via _seed_document's own
    # generated id — re-seed it under the SAME workspace as doc_a so the
    # dedupe cache lookup (tenant-scoped by RLS) can see both.
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "UPDATE documents SET workspace_id = $1 WHERE id = $2", workspace_id, doc_b
        )
    repo = PostgresIngestionRepository(worker_db_pool)
    shared_content = "duplicated content across two documents"
    await _insert_one_chunk(repo, workspace_id, doc_a, shared_content)
    await _insert_one_chunk(repo, workspace_id, doc_b, shared_content)
    async with db_bootstrap_pool.acquire() as conn:
        chunk_a = await conn.fetchrow(
            "SELECT id, content_sha256 FROM chunks WHERE document_id = $1", doc_a
        )
    vector = [0.4] * 1536
    await repo.attach_embeddings_and_advance(
        workspace_id,
        doc_a,
        chunk_embeddings=[(chunk_a["id"], vector)],
        embedding_model="text-embedding-3-small",
        embedding_version=1,
        next_status=DocumentStatus.READY,
    )

    cached = await repo.find_cached_embeddings(
        workspace_id,
        content_hashes=[chunk_a["content_sha256"]],
        embedding_model="text-embedding-3-small",
        embedding_version=1,
    )

    assert cached[chunk_a["content_sha256"]] == pytest.approx(vector)


async def test_find_cached_embeddings_ignores_a_different_embedding_version(
    worker_db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool)
    repo = PostgresIngestionRepository(worker_db_pool)
    await _insert_one_chunk(repo, workspace_id, document_id, "versioned content")
    async with db_bootstrap_pool.acquire() as conn:
        chunk = await conn.fetchrow(
            "SELECT id, content_sha256 FROM chunks WHERE document_id = $1", document_id
        )
    await repo.attach_embeddings_and_advance(
        workspace_id,
        document_id,
        chunk_embeddings=[(chunk["id"], [0.5] * 1536)],
        embedding_model="text-embedding-3-small",
        embedding_version=1,
        next_status=DocumentStatus.READY,
    )

    cached = await repo.find_cached_embeddings(
        workspace_id,
        content_hashes=[chunk["content_sha256"]],
        embedding_model="text-embedding-3-small",
        embedding_version=2,  # a hypothetical future model migration
    )

    assert cached == {}


async def test_list_chunks_returns_every_chunk_for_the_document_in_char_order(
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
        for i in reversed(range(3))  # inserted out of char order
    ]
    await repo.insert_chunks_and_advance(
        workspace_id, document_id, chunks=drafts, next_status=DocumentStatus.EMBEDDING
    )

    chunks = await repo.list_chunks(workspace_id, document_id)

    assert len(chunks) == 3  # not vacuously true for an RLS-blocked empty read
    assert [c.char_start for c in chunks] == sorted(c.char_start for c in chunks)
    assert all(c.embedding is None for c in chunks)
