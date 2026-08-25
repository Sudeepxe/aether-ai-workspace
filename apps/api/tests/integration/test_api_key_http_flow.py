"""End-to-end HTTP proof that an API key alone — no JWT — can
authenticate a real chat-message request (S10 #105, FR-API-2, §7.4):
real app, real Postgres/Redis, real bearer-token dispatch through
``get_session_or_api_key``/``get_chat_authorization``/``ChatPrincipal``,
not fakes. Mirrors test_workspace_http_flow.py's TestClient pattern.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from aether.config import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


def _register_and_login(client: TestClient, email: str) -> str:
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "s3cret!!", "display_name": email},
    )
    resp = client.post("/v1/auth/login", json={"email": email, "password": "s3cret!!"})
    token: str = resp.json()["access_token"]
    return token


@pytest.fixture()
def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: flush-on-teardown isolates rate-limit buckets
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        get_settings.cache_clear()


def _create_workspace_and_thread(
    client: TestClient, owner_headers: dict[str, str]
) -> tuple[str, str]:
    ws_resp = client.post("/v1/workspaces", json={"name": "API Key Test"}, headers=owner_headers)
    assert ws_resp.status_code == 201, ws_resp.text
    workspace_id: str = ws_resp.json()["id"]
    thread_resp = client.post(
        f"/v1/workspaces/{workspace_id}/threads", json={"title": "T"}, headers=owner_headers
    )
    assert thread_resp.status_code == 201, thread_resp.text
    thread_id: str = thread_resp.json()["id"]
    return workspace_id, thread_id


def test_api_key_alone_authenticates_a_real_chat_message_send(app_client: TestClient) -> None:
    owner_token = _register_and_login(app_client, "owner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    workspace_id, thread_id = _create_workspace_and_thread(app_client, owner_headers)

    create_key_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/api-keys",
        json={"name": "CI bot", "scopes": ["chat:write"]},
        headers=owner_headers,
    )
    assert create_key_resp.status_code == 201, create_key_resp.text
    raw_key = create_key_resp.json()["raw_key"]
    assert raw_key.startswith("aeth_")

    # The raw key must never be echoed back on any other response shape.
    list_resp = app_client.get(f"/v1/workspaces/{workspace_id}/api-keys", headers=owner_headers)
    assert list_resp.status_code == 200, list_resp.text
    assert "raw_key" not in list_resp.json()["items"][0]

    key_headers = {"Authorization": f"Bearer {raw_key}"}
    send_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={"content": "Hello from an API key", "client_message_id": uuid.uuid4().hex},
        headers=key_headers,
    )
    assert send_resp.status_code == 202, send_resp.text
    assert "generation_id" in send_resp.json()


def test_api_key_without_chat_write_scope_is_forbidden(app_client: TestClient) -> None:
    owner_token = _register_and_login(app_client, "owner2@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    workspace_id, thread_id = _create_workspace_and_thread(app_client, owner_headers)

    create_key_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/api-keys",
        json={"name": "Read-only bot", "scopes": ["kb:read"]},
        headers=owner_headers,
    )
    assert create_key_resp.status_code == 201, create_key_resp.text
    raw_key = create_key_resp.json()["raw_key"]

    send_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={"content": "Should be forbidden", "client_message_id": uuid.uuid4().hex},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert send_resp.status_code == 403, send_resp.text


def test_revoked_api_key_is_rejected(app_client: TestClient) -> None:
    owner_token = _register_and_login(app_client, "owner3@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    workspace_id, thread_id = _create_workspace_and_thread(app_client, owner_headers)

    create_key_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/api-keys",
        json={"name": "Doomed bot", "scopes": ["chat:write"]},
        headers=owner_headers,
    )
    key_id = create_key_resp.json()["id"]
    raw_key = create_key_resp.json()["raw_key"]

    revoke_resp = app_client.delete(
        f"/v1/workspaces/{workspace_id}/api-keys/{key_id}", headers=owner_headers
    )
    assert revoke_resp.status_code == 204, revoke_resp.text

    send_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        json={"content": "Should be rejected", "client_message_id": uuid.uuid4().hex},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert send_resp.status_code == 401, send_resp.text


def test_api_key_cannot_be_used_against_a_different_workspace(app_client: TestClient) -> None:
    owner_token = _register_and_login(app_client, "owner4@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    workspace_a, _ = _create_workspace_and_thread(app_client, owner_headers)

    ws_b_resp = app_client.post(
        "/v1/workspaces", json={"name": "Other workspace"}, headers=owner_headers
    )
    workspace_b = ws_b_resp.json()["id"]
    thread_b_resp = app_client.post(
        f"/v1/workspaces/{workspace_b}/threads", json={"title": "T"}, headers=owner_headers
    )
    thread_b = thread_b_resp.json()["id"]

    create_key_resp = app_client.post(
        f"/v1/workspaces/{workspace_a}/api-keys",
        json={"name": "Scoped to A", "scopes": ["chat:write"]},
        headers=owner_headers,
    )
    raw_key = create_key_resp.json()["raw_key"]

    # Presented against workspace B's URL — must get the same 404 a
    # nonexistent workspace would (§3.7.1: no existence oracle).
    send_resp = app_client.post(
        f"/v1/workspaces/{workspace_b}/threads/{thread_b}/messages",
        json={"content": "Cross-workspace attempt", "client_message_id": uuid.uuid4().hex},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert send_resp.status_code == 404, send_resp.text


async def test_a_mere_member_cannot_create_api_keys(
    app_client: TestClient, db_bootstrap_pool: asyncpg.Pool
) -> None:
    owner_token = _register_and_login(app_client, "owner5@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    workspace_id, _ = _create_workspace_and_thread(app_client, owner_headers)

    member_token = _register_and_login(app_client, "member5@example.com")
    member_headers = {"Authorization": f"Bearer {member_token}"}

    # Seeded directly as a real Member — the Admin+-only capability gate
    # itself is independently unit-tested by
    # tests/security/test_authz_matrix.py (MANAGE_API_KEYS -> ADMIN/OWNER);
    # this proves that gate is actually wired to the live HTTP route.
    async with db_bootstrap_pool.acquire() as conn:
        member_row = await conn.fetchrow("SELECT id FROM users WHERE email = 'member5@example.com'")
        await conn.execute(
            "INSERT INTO memberships (id, workspace_id, user_id, role) "
            "VALUES ($1, $2, $3, 'member')",
            uuid.uuid4(),
            uuid.UUID(workspace_id),
            member_row["id"],
        )

    denied_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/api-keys",
        json={"name": "Should be denied", "scopes": ["chat:write"]},
        headers=member_headers,
    )
    assert denied_resp.status_code == 403, denied_resp.text
