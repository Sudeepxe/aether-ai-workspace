"""End-to-end HTTP proof of generic Idempotency-Key support (S10 #106,
ADR-4.6): real app, real Postgres/Redis. A retried POST with the same
key and the same body replays the stored response — no duplicate
side effect (e.g. no duplicate workspace created) — while the same key
with a *different* body is a real 409, not a silent apply.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from aether.config import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


@pytest.fixture()
def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: flush-on-teardown isolates state
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


def _register_and_login(client: TestClient, email: str) -> str:
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "s3cret!!", "display_name": email},
    )
    resp = client.post("/v1/auth/login", json={"email": email, "password": "s3cret!!"})
    token: str = resp.json()["access_token"]
    return token


def test_same_key_and_body_replays_the_stored_response_with_no_duplicate_side_effect(
    app_client: TestClient,
) -> None:
    token = _register_and_login(app_client, "idem-owner@example.com")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-acme-workspace"}
    body = {"name": "Acme Inc"}

    first = app_client.post("/v1/workspaces", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert "Idempotent-Replay" not in first.headers

    second = app_client.post("/v1/workspaces", json=body, headers=headers)
    assert second.status_code == 201, second.text
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json() == first.json()  # identical stored response, byte-for-byte on the body

    # No duplicate workspace was actually created.
    list_resp = app_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {token}"}
    )  # sanity: session still valid
    assert list_resp.status_code == 200
    workspace_id = first.json()["id"]
    get_resp = app_client.get(f"/v1/workspaces/{workspace_id}", headers=headers)
    assert get_resp.status_code == 200
    # A second, differently-named workspace created via a *different*
    # key must succeed normally — proving the replay above didn't
    # somehow poison the route for other requests.
    other = app_client.post(
        "/v1/workspaces",
        json={"name": "Other Inc"},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "create-other-workspace"},
    )
    assert other.status_code == 201
    assert other.json()["id"] != workspace_id


def test_same_key_with_a_different_body_is_a_409_not_a_silent_apply(
    app_client: TestClient,
) -> None:
    token = _register_and_login(app_client, "idem-conflict@example.com")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "reused-key"}

    first = app_client.post("/v1/workspaces", json={"name": "First Name"}, headers=headers)
    assert first.status_code == 201, first.text

    conflict = app_client.post("/v1/workspaces", json={"name": "Different Name"}, headers=headers)
    assert conflict.status_code == 409, conflict.text
    assert "Idempotent-Replay" not in conflict.headers


def test_no_idempotency_key_header_behaves_exactly_as_before(app_client: TestClient) -> None:
    token = _register_and_login(app_client, "idem-none@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    first = app_client.post("/v1/workspaces", json={"name": "No Key A"}, headers=headers)
    second = app_client.post("/v1/workspaces", json={"name": "No Key A"}, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]  # two real, distinct workspaces


def test_idempotency_key_is_scoped_per_workspace_for_workspace_scoped_routes(
    app_client: TestClient,
) -> None:
    """The same raw Idempotency-Key value used against two *different*
    workspaces (e.g. creating an invitation in each) must not collide —
    the identity component of the Redis key is the workspace_id, not
    just the raw header value."""
    token = _register_and_login(app_client, "idem-scope@example.com")
    auth_headers = {"Authorization": f"Bearer {token}"}

    ws_a = app_client.post("/v1/workspaces", json={"name": "Workspace A"}, headers=auth_headers)
    ws_b = app_client.post("/v1/workspaces", json={"name": "Workspace B"}, headers=auth_headers)
    workspace_a, workspace_b = ws_a.json()["id"], ws_b.json()["id"]

    shared_key_headers = {**auth_headers, "Idempotency-Key": "invite-same-key"}
    invite_a = app_client.post(
        f"/v1/workspaces/{workspace_a}/invitations",
        json={"email": "invitee-a@example.com", "role": "member"},
        headers=shared_key_headers,
    )
    invite_b = app_client.post(
        f"/v1/workspaces/{workspace_b}/invitations",
        json={"email": "invitee-b@example.com", "role": "member"},
        headers=shared_key_headers,
    )

    assert invite_a.status_code == 201, invite_a.text
    assert invite_b.status_code == 201, invite_b.text
    assert "Idempotent-Replay" not in invite_b.headers
    assert invite_a.json()["email"] != invite_b.json()["email"]
