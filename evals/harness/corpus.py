"""Real ingestion for a golden case's corpus files (§6.4) — drives
``aether.app.ingestion.process_document.DocumentProcessor`` directly
(real MinIO upload, real ClamAV scan, real chunking/embedding), the
same bypass-the-queue-but-not-the-pipeline pattern
apps/api/tests/integration/test_ingestion_pipeline_e2e.py and
test_grounded_chat_e2e.py already established and proved independently
(issue #45's own tests cover the queue itself).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import httpx

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.app.ingestion.process_document import DocumentProcessor
from aether.ports.ingestion_queue import QueuedMessage

CORPORA_DIR = Path(__file__).resolve().parents[1] / "corpora"

_MIME_BY_SUFFIX = {".md": "text/markdown", ".txt": "text/plain", ".html": "text/html"}


async def ingest_corpus_files(
    *,
    workspace_id: uuid.UUID,
    filenames: list[str],
    bootstrap_pool: asyncpg.Pool,
    worker_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    """Ingests each of a case's corpus files into ``workspace_id``,
    raising if any fails to reach ``ready`` — a golden case can't run
    meaningfully against a corpus fixture that didn't actually land."""
    for filename in filenames:
        await _ingest_one(
            workspace_id=workspace_id,
            filename=filename,
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
        )


async def _ingest_one(
    *,
    workspace_id: uuid.UUID,
    filename: str,
    bootstrap_pool: asyncpg.Pool,
    worker_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    path = CORPORA_DIR / filename
    content = path.read_bytes()
    mime = _MIME_BY_SUFFIX.get(path.suffix, "text/plain")
    document_id = uuid.uuid4()
    key = f"{workspace_id}/{document_id}"

    async with bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, $3, 'x', $4, $5, $6)",
            document_id,
            workspace_id,
            filename,
            mime,
            len(content),
            key,
        )

    presigned = object_storage.presign_upload(
        key=key, content_type=mime, max_size_bytes=len(content) + 10, expires_seconds=900
    )
    upload_resp = httpx.post(
        presigned.url,
        data=presigned.fields,
        files={"file": (filename, content, mime)},
        timeout=10.0,
    )
    if upload_resp.status_code not in (200, 204):
        raise RuntimeError(f"corpus fixture {filename!r} failed to upload: {upload_resp.text}")

    host, port = clamav_endpoint
    processor = DocumentProcessor(
        object_storage=object_storage,
        scanner=ClamAvScanner(host=host, port=port),
        repository=PostgresIngestionRepository(worker_pool),
        embedder=LocalHashEmbeddingAdapter(),
    )
    await processor(
        QueuedMessage(
            stream_message_id="1-0",
            tenant_id=workspace_id,
            payload={"document_id": str(document_id), "object_key": key, "mime": mime},
            delivery_count=1,
        )
    )

    async with bootstrap_pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM documents WHERE id = $1", document_id)
    if status != "ready":
        raise RuntimeError(f"corpus fixture {filename!r} failed to ingest: status={status}")
