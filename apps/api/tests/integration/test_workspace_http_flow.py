"""End-to-end HTTP proof that the tenant-scoping chain actually works:
real app, real lifespan, real Postgres/Redis via testcontainers — not
fakes. This is the one place that proves get_workspace_scope's
SET LOCAL app.tenant_id + membership lookup + capability gating all
compose correctly through actual HTTP requests, not just through unit
tests against fakes (Sprint 1's DI-bypass bug was exactly the kind of
thing only a real end-to-end request would have caught).

The invitation-accept flow is exercised by seeding the invitation
directly via the bootstrap connection (same pattern as
test_tenancy_and_audit_rls.py) rather than round-tripping through
POST .../invitations, since the raw token is deliberately never
returned by that endpoint — in production it only ever reaches the
invitee via email (EmailPort, a later Sprint 2 milestone).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import asyncpg
import pytest
from fastapi.testclient import TestClient

from aether.app.auth.tokens import hash_token
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
    postgres_url: str, redis_url: str, monkeypatch: pytest.MonkeyPatch
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


async def test_full_workspace_membership_invitation_flow(
    app_client: TestClient, db_bootstrap_pool: asyncpg.Pool
) -> None:
    owner_token = _register_and_login(app_client, "owner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    create_resp = app_client.post(
        "/v1/workspaces", json={"name": "Acme Inc"}, headers=owner_headers
    )
    assert create_resp.status_code == 201, create_resp.text
    workspace = create_resp.json()
    workspace_id = workspace["id"]
    etag = create_resp.headers["ETag"]
    assert workspace["name"] == "Acme Inc"
    assert workspace["settings"] == {}

    get_resp = app_client.get(f"/v1/workspaces/{workspace_id}", headers=owner_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Acme Inc"

    patch_resp = app_client.patch(
        f"/v1/workspaces/{workspace_id}",
        json={"name": "Acme Corp", "settings": {"theme": "dark"}, "model_policy": {}},
        headers={**owner_headers, "If-Match": etag},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["name"] == "Acme Corp"
    assert patch_resp.json()["settings"] == {"theme": "dark"}

    stale_patch = app_client.patch(
        f"/v1/workspaces/{workspace_id}",
        json={"name": "Acme Again", "settings": {}, "model_policy": {}},
        headers={**owner_headers, "If-Match": etag},  # the now-stale ETag
    )
    assert stale_patch.status_code == 409, "stale If-Match must be rejected, not silently applied"

    members_resp = app_client.get(f"/v1/workspaces/{workspace_id}/members", headers=owner_headers)
    assert members_resp.status_code == 200
    members = members_resp.json()
    assert len(members) == 1
    assert members[0]["role"] == "owner"

    # A second, unrelated user has no standing here at all.
    outsider_token = _register_and_login(app_client, "outsider@example.com")
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}
    outsider_get = app_client.get(f"/v1/workspaces/{workspace_id}", headers=outsider_headers)
    assert outsider_get.status_code == 404, (
        "non-member must get the same 404 a nonexistent workspace would — no existence oracle"
    )
    # Confirms it's actually a 403-shaped denial wearing a 404, not a
    # generic router miss: mutating routes on the same non-membership
    # fail identically.
    outsider_patch = app_client.patch(
        f"/v1/workspaces/{workspace_id}",
        json={"name": "hijacked", "settings": {}, "model_policy": {}},
        headers={**outsider_headers, "If-Match": etag},
    )
    assert outsider_patch.status_code == 404

    # Owner invites the outsider (seeded directly — see module docstring).
    raw_token = uuid.uuid4().hex
    invitation_id = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        owner_row = await conn.fetchrow("SELECT id FROM users WHERE email = 'owner@example.com'")
        await conn.execute(
            "INSERT INTO invitations (id, workspace_id, email, role, token_hash, invited_by, expires_at) "
            "VALUES ($1, $2, 'outsider@example.com', 'member', $3, $4, now() + interval '7 days')",
            invitation_id,
            uuid.UUID(workspace_id),
            hash_token(raw_token),
            owner_row["id"],
        )

    accept_resp = app_client.post(f"/v1/invitations/{raw_token}:accept", headers=outsider_headers)
    assert accept_resp.status_code == 200, accept_resp.text
    assert accept_resp.json()["role"] == "member"

    # Reusing the same token must fail now — single-use.
    reuse_resp = app_client.post(f"/v1/invitations/{raw_token}:accept", headers=outsider_headers)
    assert reuse_resp.status_code == 404

    # The outsider is now genuinely a member and can read the workspace.
    now_member_get = app_client.get(f"/v1/workspaces/{workspace_id}", headers=outsider_headers)
    assert now_member_get.status_code == 200

    # But a mere Member can't manage other members (needs Admin+).
    members_after = app_client.get(
        f"/v1/workspaces/{workspace_id}/members", headers=owner_headers
    ).json()
    new_member_user_id = next(m["user_id"] for m in members_after if m["role"] == "member")
    demote_attempt = app_client.patch(
        f"/v1/workspaces/{workspace_id}/members/{new_member_user_id}",
        json={"role": "admin"},
        headers=outsider_headers,
    )
    assert demote_attempt.status_code == 403

    # Last-owner protection: the Owner cannot be removed (they're the
    # only one), even by themselves.
    members_current = app_client.get(
        f"/v1/workspaces/{workspace_id}/members", headers=owner_headers
    ).json()
    owner_user_id_current = next(m["user_id"] for m in members_current if m["role"] == "owner")
    remove_owner_attempt = app_client.delete(
        f"/v1/workspaces/{workspace_id}/members/{owner_user_id_current}", headers=owner_headers
    )
    assert remove_owner_attempt.status_code == 409
