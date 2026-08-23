"""End-to-end proof of issue #60's acceptance criteria (S6, ADR-6.1/6.4):
grounded chat wired all the way through — real ingestion (issue #48's
real pipeline), real hybrid retrieval, real Gate 1, real chat HTTP flow,
real persisted citations.

- A real ingested document + a matching query produces ``grounded=True``,
  a citation SSE event with correct provenance, and a persisted
  ``message_citations`` row — not a mock, the literal Postgres table.
- A redelivered/reconnected stream (idempotent replay, same
  ``client_message_id``) shows that same persisted ``grounded`` state
  and re-emits the citation, proving ``_replay_events`` reflects reality
  rather than a hardcoded default.
- A genuinely poor-scoring query against that same, non-empty knowledge
  base still produces a Gate-1 refusal when the threshold is configured
  above what a trivial single-chunk vector-leg match would clear —
  proving Gate 1 is a real confidence gate, not just an "empty KB"
  special case. (The default MVP threshold, 0.01, is documented as a
  placeholder pending Sprint 7 calibration — see config.py — and is too
  low for a single-chunk corpus to ever fail on score alone, only on
  literally zero chunks; that simpler "empty KB always refuses" proof
  already lives in test_chat_streaming_http.py. This test instead proves
  the *mechanism* generalizes to any calibrated threshold.)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest
import redis.asyncio as redis_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.app.ingestion.process_document import DocumentProcessor
from aether.config import get_settings
from aether.ports.chat import NOT_IN_KNOWLEDGE_BASE_REPLY
from aether.ports.ingestion_queue import QueuedMessage

pytestmark = pytest.mark.integration

_DOCUMENT_CONTENT = (
    b"# Zylonix Support Policy\n\n"
    b"Zylonix offers a 45-day money-back guarantee on all annual subscriptions. "
    b"This guarantee is unique to Zylonix's enterprise plan.\n\n"
    b"## Refunds\n\n"
    b"Refund requests must be submitted through the Zylonix billing portal "
    b"within the guarantee window.\n"
)


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


@pytest.fixture()
async def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: flush-on-teardown isolates rate-limit buckets
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client,
        ):
            yield client
    finally:
        get_settings.cache_clear()


@pytest.fixture()
async def strict_app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """Same as ``app_client`` but with a deliberately high refusal
    threshold — see the module docstring for why the default 0.01
    placeholder can't demonstrate a score-based refusal against a
    non-empty, single-chunk knowledge base."""
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    monkeypatch.setenv("AETHER_RETRIEVAL_REFUSAL_THRESHOLD", "1.0")
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client,
        ):
            yield client
    finally:
        get_settings.cache_clear()


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "s3cret!!", "display_name": email},
    )
    resp = await client.post("/v1/auth/login", json={"email": email, "password": "s3cret!!"})
    token: str = resp.json()["access_token"]
    return token


async def _create_workspace_and_thread(client: AsyncClient, token: str) -> tuple[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    ws_resp = await client.post(
        "/v1/workspaces", json={"name": "Grounded chat workspace"}, headers=headers
    )
    workspace_id: str = ws_resp.json()["id"]
    thread_resp = await client.post(
        f"/v1/workspaces/{workspace_id}/threads", json={"title": "T"}, headers=headers
    )
    thread_id: str = thread_resp.json()["id"]
    return workspace_id, thread_id


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue  # heartbeat comment frame
        event_type = event_id = data = None
        for line in block.split("\n"):
            if line.startswith("id: "):
                event_id = line.removeprefix("id: ")
            elif line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event_type is not None:
            frames.append(
                {"id": event_id, "event": event_type, "data": json.loads(data) if data else None}
            )
    return frames


async def _ingest_document(
    *,
    workspace_id: uuid.UUID,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
    content: bytes = _DOCUMENT_CONTENT,
) -> uuid.UUID:
    """Runs the real ingestion pipeline (issue #46/#48) directly against
    an already-real, HTTP-created workspace — real MinIO upload, real
    ClamAV scan, real Postgres persistence, real (hash-based, non-semantic
    but genuine) embeddings — the same DocumentProcessor call
    test_ingestion_pipeline_e2e.py proves independently."""
    document_id = uuid.uuid4()
    key = f"{workspace_id}/{document_id}"
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'zylonix.md', 'x', 'text/markdown', $3, $4)",
            document_id,
            workspace_id,
            len(content),
            key,
        )

    presigned = object_storage.presign_upload(
        key=key,
        content_type="text/markdown",
        max_size_bytes=len(content) + 10,
        expires_seconds=900,
    )
    files = {"file": ("zylonix.md", content, "text/markdown")}
    upload_resp = httpx.post(presigned.url, data=presigned.fields, files=files, timeout=10.0)
    assert upload_resp.status_code in (200, 204), upload_resp.text

    host, port = clamav_endpoint
    processor = DocumentProcessor(
        object_storage=object_storage,
        scanner=ClamAvScanner(host=host, port=port),
        repository=PostgresIngestionRepository(worker_db_pool),
        embedder=LocalHashEmbeddingAdapter(),
    )
    await processor(
        QueuedMessage(
            stream_message_id="1-0",
            tenant_id=workspace_id,
            payload={"document_id": str(document_id), "object_key": key, "mime": "text/markdown"},
            delivery_count=1,
        )
    )

    async with db_bootstrap_pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM documents WHERE id = $1", document_id)
    assert status == "ready", f"fixture document failed to ingest: status={status}"
    return document_id


async def test_a_real_ingested_document_produces_a_grounded_cited_answer(
    app_client: AsyncClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    token = await _register_and_login(app_client, "grounded@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    await _ingest_document(
        workspace_id=uuid.UUID(workspace_id),
        db_bootstrap_pool=db_bootstrap_pool,
        worker_db_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={
            "content": "What is Zylonix's money-back guarantee period?",
            "client_message_id": "cmid-grounded-1",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)

    assert frames[0]["event"] == "meta"
    assert frames[0]["data"]["grounded"] is True
    assert frames[-1]["event"] == "done"
    assert frames[-1]["data"]["status"] == "complete"

    citation_frames = [f for f in frames if f["event"] == "citation"]
    assert len(citation_frames) == 1
    citation_data = citation_frames[0]["data"]
    assert citation_data["document_title"] == "zylonix.md"
    assert citation_data["chunk_id"] is not None
    assert citation_data["section_path"]  # non-empty heading provenance

    token_deltas = "".join(f["data"]["delta"] for f in frames if f["event"] == "token")
    assert token_deltas != NOT_IN_KNOWLEDGE_BASE_REPLY
    assert "Zylonix" in token_deltas

    # Real persistence: the message row is grounded, and a real
    # message_citations row exists with matching provenance — not just
    # an SSE artifact.
    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    assistant = next(m for m in messages_resp.json()["items"] if m["role"] == "assistant")
    assert assistant["grounded"] is True

    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", workspace_id)
        citation_row = await conn.fetchrow(
            "SELECT document_title, section_path, chunk_id FROM message_citations "
            "WHERE message_id = $1",
            uuid.UUID(assistant["id"]),
        )
    assert citation_row is not None
    assert citation_row["document_title"] == "zylonix.md"
    assert citation_row["chunk_id"] is not None


async def test_replaying_a_grounded_reply_shows_its_real_grounded_state_and_citations(
    app_client: AsyncClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    token = await _register_and_login(app_client, "replayer@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    await _ingest_document(
        workspace_id=uuid.UUID(workspace_id),
        db_bootstrap_pool=db_bootstrap_pool,
        worker_db_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    body = {
        "content": "How long is Zylonix's guarantee?",
        "client_message_id": "cmid-grounded-replay",
    }

    first = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", json=body, headers=headers
    )
    first_frames = _parse_sse_frames(first.text)
    assert first_frames[0]["data"]["grounded"] is True

    # A retried POST with the same client_message_id — not a
    # Last-Event-ID reconnect — exercises SendMessage's own idempotent
    # replay path (_replay_events), which is what issue #60 changed to
    # stop hardcoding grounded=False.
    second = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", json=body, headers=headers
    )
    assert second.status_code == 200
    second_frames = _parse_sse_frames(second.text)

    assert second_frames[0]["event"] == "meta"
    assert second_frames[0]["data"]["grounded"] is True
    citation_frames = [f for f in second_frames if f["event"] == "citation"]
    assert len(citation_frames) == 1
    assert citation_frames[0]["data"]["document_title"] == "zylonix.md"

    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    assert len(messages_resp.json()["items"]) == 2, "a replay must not create duplicate messages"


async def test_a_poor_scoring_query_refuses_under_a_real_calibrated_threshold(
    strict_app_client: AsyncClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    token = await _register_and_login(strict_app_client, "refuser@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(strict_app_client, token)
    await _ingest_document(
        workspace_id=uuid.UUID(workspace_id),
        db_bootstrap_pool=db_bootstrap_pool,
        worker_db_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    resp = await strict_app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={
            "content": "What is the boiling point of tungsten?",
            "client_message_id": "cmid-refusal-1",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)

    assert frames[0]["event"] == "meta"
    assert frames[0]["data"]["grounded"] is False
    token_deltas = "".join(f["data"]["delta"] for f in frames if f["event"] == "token")
    assert token_deltas == NOT_IN_KNOWLEDGE_BASE_REPLY
    assert not any(f["event"] == "citation" for f in frames)
    assert frames[-1]["data"]["status"] == "complete"
