"""End-to-end HTTP proof of the streaming spine (Sprint 3):

- issue #26's literal acceptance criterion: a streamed echo chat e2e
  test passes — real app, real Postgres/Redis, real SSE response.
- issue #25's literal acceptance criterion: a reconnect test proves
  duplicate-by-seq events are discarded on Last-Event-ID resume.

Uses httpx.AsyncClient + ASGITransport (not the sync TestClient) since
later tests in this file need genuine concurrency; asgi-lifespan's
LifespanManager runs the app's startup/shutdown the way TestClient's
context manager normally would.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import redis.asyncio as redis_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from aether.config import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.security]


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
    ws_resp = await client.post("/v1/workspaces", json={"name": "Chat workspace"}, headers=headers)
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


async def test_echo_chat_streams_and_persists_the_reply(app_client: AsyncClient) -> None:
    token = await _register_and_login(app_client, "chatter@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={"content": "hello world", "client_message_id": "cmid-1"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse_frames(resp.text)
    assert frames[0]["event"] == "meta"
    assert frames[0]["data"]["model"] == "echo-v1"
    assert frames[-1]["event"] == "done"
    assert frames[-1]["data"]["status"] == "complete"
    assert frames[-2]["event"] == "usage"

    token_deltas = "".join(f["data"]["delta"] for f in frames if f["event"] == "token")
    assert token_deltas == "hello world"

    # seq is strictly monotonic and gapless across the whole SSE response.
    seqs = [int(f["id"].rsplit(":", 1)[1]) for f in frames]
    assert seqs == list(range(len(frames)))

    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    items = messages_resp.json()["items"]
    assert len(items) == 2
    assistant = next(m for m in items if m["role"] == "assistant")
    user = next(m for m in items if m["role"] == "user")
    assert assistant["content"] == "hello world"
    assert assistant["status"] == "complete"
    assert assistant["seq"] == user["seq"] + 1


async def test_retried_post_with_same_client_message_id_does_not_duplicate(
    app_client: AsyncClient,
) -> None:
    token = await _register_and_login(app_client, "retrier@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    body = {"content": "retried content", "client_message_id": "cmid-retry"}

    first = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", json=body, headers=headers
    )
    second = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", json=body, headers=headers
    )
    assert first.status_code == second.status_code == 200

    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    assert len(messages_resp.json()["items"]) == 2, (
        "a retried POST must not create duplicate messages"
    )


async def test_reconnect_with_last_event_id_does_not_redeliver_earlier_events(
    app_client: AsyncClient,
) -> None:
    token = await _register_and_login(app_client, "resumer@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    body = {"content": "one two three four five", "client_message_id": "cmid-resume"}

    first_resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", json=body, headers=headers
    )
    first_frames = _parse_sse_frames(first_resp.text)
    first_seqs = [int(f["id"].rsplit(":", 1)[1]) for f in first_frames]
    assert len(first_frames) >= 4  # meta + several tokens + usage + done

    # A reconnect that already saw meta + the first token (seq 0 and 1).
    resume_from_id = first_frames[1]["id"]
    seen_seq = int(resume_from_id.rsplit(":", 1)[1])

    resume_resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json=body,
        headers={**headers, "Last-Event-ID": resume_from_id},
    )
    assert resume_resp.status_code == 200
    resumed_frames = _parse_sse_frames(resume_resp.text)
    resumed_seqs = [int(f["id"].rsplit(":", 1)[1]) for f in resumed_frames]

    assert all(seq > seen_seq for seq in resumed_seqs), (
        "resume must not redeliver already-seen seqs"
    )
    assert resumed_seqs == first_seqs[first_seqs.index(seen_seq) + 1 :], (
        "resume must deliver exactly the remaining seqs, no gaps or dupes"
    )
    assert resumed_frames[-1]["event"] == "done"


async def test_reconnect_to_an_unknown_generation_id_404s(app_client: AsyncClient) -> None:
    token = await _register_and_login(app_client, "unknown-resumer@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Last-Event-ID": "00000000-0000-7000-8000-000000000000:0",
    }

    resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={"content": "hi", "client_message_id": "cmid-unknown"},
        headers=headers,
    )

    assert resp.status_code == 404
