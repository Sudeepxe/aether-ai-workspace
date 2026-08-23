"""Issue #27's literal acceptance criterion: a multi-replica-simulated
test proves resume and cancel both work when the initiating and
reconnecting/cancelling requests land on different replicas.

Two independent app instances (separate Containers — separate connection
pools and Redis clients, no shared Python objects) point at the same
real Postgres/Redis, standing in for two replicas behind a load
balancer. Generation, once started via replica A, keeps running as an
independent background task (http/sse.py's ``run_generation_in_background``)
regardless of what replica A's own client does next — that decoupling is
exactly what makes "no sticky sessions" (§3.9.2) true rather than
aspirational (Ch.3 self-review F-3). This test proves it by partially
reading replica A's stream, resuming (also partially) from replica B,
cancelling from replica B, and then checking replica A's generation
actually terminates as cancelled with a shorter-than-full reply.

Since issue #60 (S6), every chat turn is grounded — a document is
ingested first (real pipeline, same as test_grounded_chat_e2e.py) so
EchoGenerator's grounded reply has real multi-word content to stream
over real wall-clock time; a document-less workspace's single-token
refusal reply would complete before either replica could read more than
one frame, defeating this test's whole premise.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest
import redis.asyncio as redis_asyncio
import uvicorn
from fastapi import FastAPI
from httpx import AsyncClient

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.app.ingestion.process_document import DocumentProcessor
from aether.config import get_settings
from aether.ports.ingestion_queue import QueuedMessage

pytestmark = [pytest.mark.integration, pytest.mark.security]

_TIMEOUT_SECONDS = 20.0
_CHUNK_WORD_COUNT = 56
# EchoGenerator's grounded reply prefixes the chunk content with
# "Grounded on: {title} ({section}): " (4 more space-split words) — see
# adapters/echo/generator.py's _build_reply.
_WORD_COUNT = _CHUNK_WORD_COUNT + 4


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


async def _start_server(app: FastAPI) -> tuple[uvicorn.Server, asyncio.Task[None], int]:
    """Real TCP, not httpx's ASGITransport: ASGITransport runs the whole
    app call to completion (collecting the full response body) before
    ``handle_async_request`` returns anything at all — it cannot deliver
    partial chunks to a concurrently-running reader. This test's whole
    point is genuine concurrency (reading replica A's in-flight stream
    while issuing requests to replica B), which only a real socket-based
    transport supports. uvicorn is already a direct dependency (it's how
    the app is actually served), so this adds nothing new."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, port


@pytest.fixture()
async def two_replicas(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: flush-on-teardown isolates rate-limit buckets
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app_a = create_app()
        app_b = create_app()
        server_a, task_a, port_a = await _start_server(app_a)
        server_b, task_b, port_b = await _start_server(app_b)
        try:
            async with (
                AsyncClient(base_url=f"http://127.0.0.1:{port_a}") as client_a,
                AsyncClient(base_url=f"http://127.0.0.1:{port_b}") as client_b,
            ):
                yield client_a, client_b
        finally:
            server_a.should_exit = True
            server_b.should_exit = True
            await asyncio.gather(task_a, task_b)
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
    ws_resp = await client.post("/v1/workspaces", json={"name": "Multi replica"}, headers=headers)
    workspace_id: str = ws_resp.json()["id"]
    thread_resp = await client.post(
        f"/v1/workspaces/{workspace_id}/threads", json={"title": "T"}, headers=headers
    )
    thread_id: str = thread_resp.json()["id"]
    return workspace_id, thread_id


async def _ingest_document(
    *,
    workspace_id: uuid.UUID,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    """Real ingestion (issue #46/#48), trimmed down from
    test_grounded_chat_e2e.py's version — this test only needs *a*
    ready chunk with enough words to stream, not specific content."""
    document_id = uuid.uuid4()
    key = f"{workspace_id}/{document_id}"
    content = (
        "# Streaming filler\n\n" + " ".join(f"filler{i}" for i in range(_CHUNK_WORD_COUNT))
    ).encode()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'filler.md', 'x', 'text/markdown', $3, $4)",
            document_id,
            workspace_id,
            len(content),
            key,
        )

    presigned = object_storage.presign_upload(
        key=key, content_type="text/markdown", max_size_bytes=len(content) + 10, expires_seconds=900
    )
    files = {"file": ("filler.md", content, "text/markdown")}
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


def _parse_sse_block(block: str) -> dict[str, Any] | None:
    if not block.strip() or block.startswith(":"):
        return None
    event_type = event_id = data = None
    for line in block.split("\n"):
        if line.startswith("id: "):
            event_id = line.removeprefix("id: ")
        elif line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = line.removeprefix("data: ")
    if event_type is None:
        return None
    return {"id": event_id, "event": event_type, "data": json.loads(data) if data else None}


def _parse_sse_text(text: str) -> list[dict[str, Any]]:
    return [f for b in text.strip("\n").split("\n\n") if (f := _parse_sse_block(b)) is not None]


def _seq_of(frame: dict[str, Any]) -> int:
    return int(frame["id"].rsplit(":", 1)[1])


async def _read_frames_until(
    client: AsyncClient, url: str, body: dict[str, Any], headers: dict[str, str], *, min_frames: int
) -> list[dict[str, Any]]:
    """Reads an SSE response incrementally, stopping as soon as either
    ``min_frames`` have arrived or the stream reaches ``done`` —
    whichever comes first. Exiting the ``stream()`` context early
    abandons the client-side connection (a real disconnect from the
    app's point of view) without affecting the server's independent
    background generation task, which is exactly the scenario this test
    needs to create."""
    collected: list[dict[str, Any]] = []
    async with client.stream("POST", url, json=body, headers=headers) as response:
        assert response.status_code == 200
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                frame = _parse_sse_block(block)
                if frame is None:
                    continue
                collected.append(frame)
                if frame["event"] == "done" or len(collected) >= min_frames:
                    return collected
    return collected


async def test_resume_and_cancel_both_work_across_a_different_replica(
    two_replicas: tuple[AsyncClient, AsyncClient],
    worker_db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    client_a, client_b = two_replicas
    token = await _register_and_login(client_a, "multireplica@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(client_a, token)
    await _ingest_document(
        workspace_id=uuid.UUID(workspace_id),
        db_bootstrap_pool=db_bootstrap_pool,
        worker_db_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )
    url = f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    # The reply's word count now comes from the ingested chunk's content
    # (EchoGenerator echoes retrieved context, not the user's own
    # message, since #60) — ~_WORD_COUNT words is enough real wall-clock
    # time at EchoGenerator's 20ms/word to read a few frames from two
    # different replicas and still cancel well before natural completion.
    body = {
        "content": "Tell me about the filler content.",
        "client_message_id": "cmid-multi-replica",
    }

    # --- Replica A starts the generation, reads a handful of frames. ---
    frames_from_a = await asyncio.wait_for(
        _read_frames_until(client_a, url, body, headers, min_frames=4), timeout=_TIMEOUT_SECONDS
    )
    assert len(frames_from_a) >= 2
    assert frames_from_a[0]["event"] == "meta"
    generation_id = frames_from_a[0]["data"]["generation_id"]
    last_seen_seq = _seq_of(frames_from_a[-1])

    # --- Replica B resumes from where A left off, reads a few more. ---
    resumed_frames = await asyncio.wait_for(
        _read_frames_until(
            client_b,
            url,
            body,
            {**headers, "Last-Event-ID": frames_from_a[-1]["id"]},
            min_frames=3,
        ),
        timeout=_TIMEOUT_SECONDS,
    )
    assert resumed_frames, "replica B must be able to read replica A's in-flight generation"
    assert all(_seq_of(f) > last_seen_seq for f in resumed_frames), (
        "resume must not redeliver seen seqs"
    )
    assert resumed_frames[-1]["event"] != "done", (
        "generation should still be in-flight with this much runway left"
    )

    # --- Replica B cancels. ---
    cancel_resp = await client_b.delete(
        f"/v1/workspaces/{workspace_id}/generations/{generation_id}", headers=headers
    )
    assert cancel_resp.status_code == 204

    # --- Replica A's independent background task must observe the
    # cancellation (published from replica B) and terminate. ---
    async def wait_for_done() -> None:
        while True:
            status_resp = await client_a.get(
                f"/v1/workspaces/{workspace_id}/generations/{generation_id}", headers=headers
            )
            if status_resp.status_code == 200 and status_resp.json()["is_done"]:
                return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(wait_for_done(), timeout=_TIMEOUT_SECONDS)

    last_overall_seq = _seq_of(resumed_frames[-1])
    final_resp = await client_a.post(
        url, json=body, headers={**headers, "Last-Event-ID": f"{generation_id}:{last_overall_seq}"}
    )
    final_frames = _parse_sse_text(final_resp.text)
    assert final_frames, "the tail read after cancellation must still return the done event"
    assert final_frames[-1]["event"] == "done"
    assert final_frames[-1]["data"]["status"] == "cancelled"

    # --- The persisted assistant message reflects the cancellation. ---
    messages_resp = await client_b.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    items = messages_resp.json()["items"]
    assistant = next(m for m in items if m["role"] == "assistant")
    assert assistant["status"] == "cancelled"
    assert len(assistant["content"].split()) < _WORD_COUNT, (
        "cancellation must have cut generation short"
    )
