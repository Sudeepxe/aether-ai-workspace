"""Chaos-lite experiment 3/3 (S9 #97, §10.5): kill a provider mid-
stream, assert SD-1 fallback + partial-response handling — through the
*real* HTTP/SSE surface (real app, real Postgres, a real streamed
response parsed off the wire), not the use-case-with-fakes level
``test_send_message.py`` already covers exhaustively
(``test_provider_error_mid_stream_persists_partial_content`` et al.).
This experiment's job is narrower and different: prove the same
contract survives the real ASGI/SSE transport and a real persisted-
message round trip, not re-derive it.

Needs a real ingested document (same fixture-building pattern as
``test_grounded_chat_e2e.py``) so Gate 1 (ADR-6.4) actually clears and
the generator gets called at all — an empty-KB turn refuses before ever
reaching the generator (``test_gate_1_refusal_short_circuits_before_any_generator_call``),
which would make this experiment vacuous.

There's no separate "provider stub" *process* in this architecture to
docker-kill (OpenAI/Anthropic adapters call real external APIs over
httpx; EchoGenerator is the in-process placeholder) — the container's
own composition root (``http/composition.py``) is swapped post-startup
with a ``GeneratorPort`` that fails after one token, which is the
correct way to inject "a provider died mid-stream" at this boundary.
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
from aether.ports.ingestion_queue import QueuedMessage
from aether.ports.llm import ProviderError
from tests.unit.fakes.chat import FakeGenerator

pytestmark = pytest.mark.chaos

_DOCUMENT_CONTENT = (
    b"# Chaos Corp Support Policy\n\n"
    b"Chaos Corp offers a 45-day money-back guarantee on all annual subscriptions.\n"
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
            # The chaos injection point: swap the real composition
            # root's generator, post-startup, for one that behaves like
            # a provider that answered one token and then died —
            # mirroring exactly what LlmRouter's own fallback-exhausted
            # path surfaces to the orchestrator (ProviderError after
            # partial output, no further fallback since text already
            # reached the caller — router.py's own "retry only before
            # the first streamed token" rule).
            #
            # container.send_message is already a fully-constructed
            # SendMessage use case by the time lifespan startup
            # finishes (http/composition.py wires it once, generator
            # included, at container-build time) — reassigning
            # container.generator itself wouldn't reach it, since
            # SendMessage holds its own private reference, not a live
            # pointer back to the container. Reaching into that private
            # attribute is the only seam available without adding
            # test-only production code; there's no cleaner DI hook for
            # a single already-built use-case's dependency today.
            app.state.container.send_message._generator = FakeGenerator(
                deltas=["Partial answer before the "],
                error=ProviderError("connection reset mid-stream", retryable=True),
                fail_after=1,
            )
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


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue  # heartbeat comment frame
        event_type = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event_type is not None:
            frames.append({"event": event_type, "data": json.loads(data) if data else None})
    return frames


async def _ingest_document(
    *,
    workspace_id: uuid.UUID,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    """Same real-pipeline pattern as test_grounded_chat_e2e.py's own
    ``_ingest_document`` — duplicated locally rather than imported
    cross-module, matching this test suite's existing per-file-local-
    helper convention (no shared test-helpers module exists to import
    from instead)."""
    document_id = uuid.uuid4()
    key = f"{workspace_id}/{document_id}"
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'chaos.md', 'x', 'text/markdown', $3, $4)",
            document_id,
            workspace_id,
            len(_DOCUMENT_CONTENT),
            key,
        )

    presigned = object_storage.presign_upload(
        key=key,
        content_type="text/markdown",
        max_size_bytes=len(_DOCUMENT_CONTENT) + 10,
        expires_seconds=900,
    )
    files = {"file": ("chaos.md", _DOCUMENT_CONTENT, "text/markdown")}
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


async def test_a_provider_dying_mid_stream_yields_a_clean_partial_sse_state_not_a_hang_or_500(
    app_client: AsyncClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    token = await _register_and_login(app_client, "chaos-provider@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    ws_resp = await app_client.post(
        "/v1/workspaces", json={"name": "Chaos Provider"}, headers=headers
    )
    workspace_id = ws_resp.json()["id"]
    await _ingest_document(
        workspace_id=uuid.UUID(workspace_id),
        db_bootstrap_pool=db_bootstrap_pool,
        worker_db_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )
    thread_resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads", json={"title": "T"}, headers=headers
    )
    thread_id = thread_resp.json()["id"]

    resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={
            "content": "What is Chaos Corp's money-back guarantee period?",
            "client_message_id": "chaos-mid-stream-1",
        },
        headers={**headers, "Accept": "text/event-stream"},
    )

    # A real HTTP response came back, complete and well-formed — not a
    # hang, not a raw 500, not a truncated/corrupted stream.
    assert resp.status_code == 200
    frames = _parse_sse_frames(resp.text)
    assert frames[0]["event"] == "meta"
    assert frames[0]["data"]["grounded"] is True  # confirms Gate 1 cleared, generator was reached

    error_frames = [f for f in frames if f["event"] == "error"]
    assert len(error_frames) == 1
    assert error_frames[0]["data"]["code"] == "provider_error"
    assert frames[-1]["event"] == "done"
    assert frames[-1]["data"]["status"] == "partial"

    # And the partial content genuinely persisted — a follow-up GET
    # sees exactly the same message a client reconnecting mid-incident
    # would, not a message stuck forever in a "generating" limbo state.
    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    bodies = [m["content"] for m in messages_resp.json()["items"] if m["role"] == "assistant"]
    assert bodies == ["Partial answer before the "]
