"""Chaos-lite experiment 2/3 (S9 #97, §10.5): kill a worker mid-ingest,
assert resume — via real Redis Streams consumer-group semantics (the
same PEL-based redelivery already proven at the queue-mechanics level
by issue #45's own suite, ``test_a_failed_message_is_redelivered_with_incremented_delivery_count``),
not a literal ``kill -9`` on a subprocess.

This is deliberately the same "reproduce the resulting *state* a crash
leaves behind, not the literal kill signal" pattern this codebase
already uses (``test_dispatch_workspace_deletion.py``'s
``test_a_redelivered_already_complete_job_is_a_safe_no_op`` reasons
about this explicitly) — a worker SIGKILLed after ``claim_next()``
returns but before ``ack()``/``fail()`` runs leaves Redis in exactly
one state: the message stays claimed-but-unacked in the consumer
group's PEL. Simulating that state directly (claim, then simply never
ack) exercises the identical redelivery path a real kill would trigger,
without the flakiness of timing a literal process kill against a real
malware-scan/parse/chunk/embed pipeline's non-deterministic duration.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest
import redis.asyncio as redis_asyncio

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.adapters.redis.ingestion_queue import RedisIngestionQueue
from aether.app.ingestion.process_document import DocumentProcessor
from aether.domain.entities import DocumentStatus

pytestmark = pytest.mark.chaos


async def _seed_document(bootstrap_pool: asyncpg.Pool, *, mime: str) -> tuple[uuid.UUID, uuid.UUID]:
    workspace_id, document_id = uuid.uuid4(), uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Chaos Ingestion', $2)",
            workspace_id,
            f"chaos-ingestion-{workspace_id}",
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
    presigned = object_storage.presign_upload(
        key=key,
        content_type="text/markdown",
        max_size_bytes=len(content) + 10,
        expires_seconds=900,
    )
    files = {"file": ("f.md", content, "text/markdown")}
    resp = httpx.post(presigned.url, data=presigned.fields, files=files, timeout=10.0)
    assert resp.status_code in (200, 204), resp.text


async def test_a_message_claimed_but_never_acked_survives_a_simulated_worker_kill_and_resumes(
    db_bootstrap_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
    redis_client: redis_asyncio.Redis,
) -> None:
    workspace_id, document_id = await _seed_document(db_bootstrap_pool, mime="text/markdown")
    key = f"{workspace_id}/{document_id}"
    await _upload(object_storage, key=key, content=b"# Chaos\n\nSurvives a worker kill mid-ingest.")

    queue = RedisIngestionQueue(redis_client, consumer_name="worker-1-about-to-die")
    await queue.enqueue(
        tenant_id=workspace_id,
        payload={"document_id": str(document_id), "object_key": key, "mime": "text/markdown"},
    )

    # "Worker 1" claims the message — this is the exact point a real
    # SIGKILL would land mid-pipeline (after XREADGROUP, before ack) —
    # then dies without ever calling ack()/fail().
    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed.delivery_count == 1

    ingestion_repository = PostgresIngestionRepository(db_bootstrap_pool)
    scanner = ClamAvScanner(host=clamav_endpoint[0], port=clamav_endpoint[1])
    processor = DocumentProcessor(
        object_storage=object_storage,
        scanner=scanner,
        repository=ingestion_repository,
        embedder=LocalHashEmbeddingAdapter(),
    )

    # Never processed, never acked — the document is still wherever it
    # started (never advanced past its initial DB row), proving the
    # "dead" worker genuinely did nothing observable.
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        status = await conn.fetchval("SELECT status FROM documents WHERE id = $1", document_id)
    assert status == DocumentStatus.QUEUED.value

    # "Worker 2" starts up (a fresh RedisIngestionQueue instance, a
    # fresh consumer name — a real replacement process, not the same
    # object) and claims via the pending-retry path, since the message
    # is still sitting unacked in the consumer group's PEL.
    replacement_queue = RedisIngestionQueue(redis_client, consumer_name="worker-2-replacement")
    resumed = await replacement_queue.claim_next()
    assert resumed is not None
    assert resumed.stream_message_id == claimed.stream_message_id
    assert resumed.delivery_count == 2  # redelivered, not a fresh message
    assert resumed.payload == claimed.payload

    await processor(resumed)
    await replacement_queue.ack(resumed)

    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        final_status = await conn.fetchval(
            "SELECT status FROM documents WHERE id = $1", document_id
        )
    assert final_status == DocumentStatus.READY.value
