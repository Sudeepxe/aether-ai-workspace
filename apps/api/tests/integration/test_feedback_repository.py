"""Real-Postgres proof for feedback persistence (issue #83, FR-CH-6, §8.1)
— runs as app_api, the same role the running HTTP process uses.

Proves the three things a fake store can't: RLS actually isolates
workspaces, the UNIQUE(message_id, user_id) constraint really makes
ON CONFLICT (message_id, user_id) DO UPDATE a genuine "latest-wins per
(message, user)" upsert (not just an insert that happens to work in the
fake), and the real HTTP round trip (submit, then GET messages) threads
the caller's own feedback back onto the message.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from aether.adapters.postgres.feedback_repository import PostgresFeedbackRepository
from aether.config import get_settings
from aether.domain.entities import FeedbackRating

pytestmark = pytest.mark.integration


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
    ws_resp = await client.post(
        "/v1/workspaces", json={"name": "Feedback workspace"}, headers=headers
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


async def _send_message_and_get_assistant_message_id(
    client: AsyncClient, token: str, workspace_id: str, thread_id: str, *, client_message_id: str
) -> str:
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    resp = await client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={"content": "What's the refund policy?", "client_message_id": client_message_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse_frames(resp.text)
    messages_resp = await client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assistant_item = next(
        item for item in messages_resp.json()["items"] if item["role"] == "assistant"
    )
    assert frames[0]["event"] == "meta"
    message_id: str = assistant_item["id"]
    return message_id


async def _seed_workspace_thread_assistant_message(
    bootstrap_pool: asyncpg.Pool,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (workspace_id, thread_id, message_id, user_id)."""
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Feedback Test', $2)",
            workspace_id,
            f"feedback-test-{workspace_id}",
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
            "grounded) VALUES ($1, $2, $3, 1, 'assistant', 'Acme costs $10/mo.', 'complete', false)",
            message_id,
            workspace_id,
            thread_id,
        )
    return workspace_id, thread_id, message_id, user_id


async def test_feedback_is_created_and_read_back(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, _, message_id, user_id = await _seed_workspace_thread_assistant_message(
        db_bootstrap_pool
    )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        repo = PostgresFeedbackRepository(conn)
        created = await repo.upsert(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            message_id=message_id,
            user_id=user_id,
            rating=FeedbackRating.UP,
            reason=None,
        )
        [fetched] = await repo.list_by_messages_for_user(workspace_id, [message_id], user_id)

    assert fetched.id == created.id
    assert fetched.rating == FeedbackRating.UP


async def test_upsert_on_conflict_replaces_rather_than_duplicates(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """The literal 'latest-wins per (message, user)' contract (§8.1) — a
    caller changing their mind overwrites, it doesn't append a second row."""
    workspace_id, _, message_id, user_id = await _seed_workspace_thread_assistant_message(
        db_bootstrap_pool
    )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        repo = PostgresFeedbackRepository(conn)
        await repo.upsert(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            message_id=message_id,
            user_id=user_id,
            rating=FeedbackRating.UP,
            reason=None,
        )
        second = await repo.upsert(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            message_id=message_id,
            user_id=user_id,
            rating=FeedbackRating.DOWN,
            reason="actually wrong",
        )
        row_count = await conn.fetchval(
            "SELECT count(*) FROM feedback WHERE message_id = $1 AND user_id = $2",
            message_id,
            user_id,
        )
        [fetched] = await repo.list_by_messages_for_user(workspace_id, [message_id], user_id)

    assert row_count == 1
    assert fetched.id == second.id
    assert fetched.rating == FeedbackRating.DOWN
    assert fetched.reason == "actually wrong"


async def test_feedback_cannot_be_read_across_workspaces(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_a, _, message_a, user_a = await _seed_workspace_thread_assistant_message(
        db_bootstrap_pool
    )
    workspace_b, _, _, _ = await _seed_workspace_thread_assistant_message(db_bootstrap_pool)

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_a))
        await PostgresFeedbackRepository(conn).upsert(
            id=uuid.uuid4(),
            workspace_id=workspace_a,
            message_id=message_a,
            user_id=user_a,
            rating=FeedbackRating.UP,
            reason=None,
        )

    # RLS, not just the repository's own explicit WHERE clause, is what
    # must block this: the connection's tenant context is workspace_b,
    # but the call itself claims the *correct* (workspace_a, message_a).
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_b))
        cross_tenant_view = await PostgresFeedbackRepository(conn).list_by_messages_for_user(
            workspace_a, [message_a], user_a
        )

    assert cross_tenant_view == []


async def test_submit_feedback_http_round_trip_threads_onto_get_messages(
    app_client: AsyncClient,
) -> None:
    """The literal issue #83 acceptance scenario end-to-end: real
    register+login, real thread+message send, real POST feedback, and a
    real GET messages showing that feedback threaded back onto the item."""
    token = await _register_and_login(app_client, "feedback-giver@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    message_id = await _send_message_and_get_assistant_message_id(
        app_client, token, workspace_id, thread_id, client_message_id="cmid-feedback-1"
    )
    headers = {"Authorization": f"Bearer {token}"}

    feedback_resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages/{message_id}/feedback",
        json={"rating": "up", "reason": "Fast and accurate"},
        headers=headers,
    )
    assert feedback_resp.status_code == 200, feedback_resp.text
    assert feedback_resp.json() == {"rating": "up", "reason": "Fast and accurate"}

    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    assistant_item = next(
        item for item in messages_resp.json()["items"] if item["id"] == message_id
    )
    assert assistant_item["feedback"] == {"rating": "up", "reason": "Fast and accurate"}


async def test_feedback_on_a_user_message_is_rejected_over_http(
    app_client: AsyncClient,
) -> None:
    token = await _register_and_login(app_client, "wrong-target@example.com")
    workspace_id, thread_id = await _create_workspace_and_thread(app_client, token)
    headers = {"Authorization": f"Bearer {token}"}
    await _send_message_and_get_assistant_message_id(
        app_client, token, workspace_id, thread_id, client_message_id="cmid-feedback-2"
    )
    messages_resp = await app_client.get(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages", headers=headers
    )
    user_item = next(item for item in messages_resp.json()["items"] if item["role"] == "user")

    resp = await app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages/{user_item['id']}/feedback",
        json={"rating": "up"},
        headers=headers,
    )

    assert resp.status_code == 422
