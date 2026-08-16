"""End-to-end proof that the ingestion pipeline stages (issue #46)
genuinely work together against real infrastructure — real MinIO, real
ClamAV, real Postgres — not just individually mocked. Drives
DocumentProcessor directly (bypassing the Redis queue itself, which
issue #45's own test suite already proves independently) so this test's
job is specifically "do fetch -> scan -> parse -> chunk -> persist
compose correctly," not re-proving queue mechanics.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.app.ingestion.process_document import DocumentProcessor
from aether.ports.ingestion_queue import QueuedMessage

pytestmark = pytest.mark.integration


async def _seed_document(bootstrap_pool: asyncpg.Pool, *, mime: str) -> tuple[uuid.UUID, uuid.UUID]:
    workspace_id, document_id = uuid.uuid4(), uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'E2E Ingestion', $2)",
            workspace_id,
            f"e2e-ingestion-{workspace_id}",
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.md', 'x', $3, 100, $4)",
            document_id,
            workspace_id,
            mime,
            f"{workspace_id}/{document_id}",
        )
    return workspace_id, document_id


async def _upload(object_storage: MinioObjectStorage, *, key: str, content: bytes) -> None:
    import httpx

    presigned = object_storage.presign_upload(
        key=key,
        content_type="text/markdown",
        max_size_bytes=len(content) + 10,
        expires_seconds=900,
    )
    files = {"file": ("f.md", content, "text/markdown")}
    resp = httpx.post(presigned.url, data=presigned.fields, files=files, timeout=10.0)
    assert resp.status_code in (200, 204), resp.text


async def test_a_real_markdown_document_is_processed_end_to_end(
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
    worker_db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool, mime="text/markdown")
    key = f"{workspace_id}/{document_id}"
    content = (
        b"# Introduction\n\n"
        b"This is a real document processed end to end. "
        b"It has enough content to form at least one real chunk.\n\n"
        b"## Details\n\n"
        b"Some more detail text goes here for good measure.\n"
    )
    await _upload(object_storage, key=key, content=content)

    host, port = clamav_endpoint
    processor = DocumentProcessor(
        object_storage=object_storage,
        scanner=ClamAvScanner(host=host, port=port),
        repository=PostgresIngestionRepository(worker_db_pool),
        embedder=LocalHashEmbeddingAdapter(),
    )
    message = QueuedMessage(
        stream_message_id="1-0",
        tenant_id=workspace_id,
        payload={"document_id": str(document_id), "object_key": key, "mime": "text/markdown"},
        delivery_count=1,
    )

    await processor(message)

    async with db_bootstrap_pool.acquire() as conn:
        doc_row = await conn.fetchrow(
            "SELECT status, failure_stage, failure_reason FROM documents WHERE id = $1",
            document_id,
        )
        chunk_rows = await conn.fetch(
            "SELECT content, section_path, token_count, embedding, embedding_model "
            "FROM chunks WHERE document_id = $1 ORDER BY char_start",
            document_id,
        )
        outbox_row = await conn.fetchrow(
            "SELECT event_type, payload FROM outbox WHERE aggregate_id = $1 "
            "AND event_type = 'document.ready'",
            document_id,
        )

    assert doc_row["status"] == "ready", doc_row["failure_reason"]
    assert doc_row["failure_stage"] is None
    assert len(chunk_rows) >= 1
    combined = " ".join(r["content"] for r in chunk_rows)
    assert "real document processed end to end" in combined
    # Sub-headings (level > 1) don't force a new chunk, so this short
    # document lands in one chunk whose section_path reflects the
    # deepest heading active by the time it closes ("Introduction >
    # Details") — proving heading-derived provenance survived the whole
    # pipeline without over-asserting an exact hierarchy depth.
    assert any("Introduction" in r["section_path"] for r in chunk_rows)
    assert all(r["embedding"] is not None for r in chunk_rows)
    assert all(r["embedding_model"] == "local-hash-fallback" for r in chunk_rows)
    assert outbox_row is not None
    assert outbox_row["payload"]["document_id"] == str(document_id)


async def test_a_malicious_document_is_rejected_end_to_end(
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
    worker_db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool, mime="text/plain")
    key = f"{workspace_id}/{document_id}"
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    await _upload(object_storage, key=key, content=eicar)

    host, port = clamav_endpoint
    processor = DocumentProcessor(
        object_storage=object_storage,
        scanner=ClamAvScanner(host=host, port=port),
        repository=PostgresIngestionRepository(worker_db_pool),
        embedder=LocalHashEmbeddingAdapter(),
    )
    message = QueuedMessage(
        stream_message_id="1-0",
        tenant_id=workspace_id,
        payload={"document_id": str(document_id), "object_key": key, "mime": "text/plain"},
        delivery_count=1,
    )

    await processor(message)

    async with db_bootstrap_pool.acquire() as conn:
        doc_row = await conn.fetchrow(
            "SELECT status, failure_stage, failure_reason FROM documents WHERE id = $1",
            document_id,
        )
        chunk_count = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", document_id
        )
        outbox_row = await conn.fetchrow(
            "SELECT payload FROM outbox WHERE aggregate_id = $1 AND event_type = 'document.failed'",
            document_id,
        )

    assert doc_row["status"] == "failed"
    assert doc_row["failure_stage"] == "scanning"
    assert "eicar" in doc_row["failure_reason"].lower()
    assert chunk_count == 0
    assert outbox_row is not None
    assert outbox_row["payload"]["stage"] == "scanning"
