"""Proves FR-AD-1's auth-event coverage directly: register/login/logout
actually write to audit_events, not just "the flow didn't crash" (which
the broader end-to-end workspace test would already show if this were
silently broken — a passing HTTP response doesn't prove the audit write
happened, only that it didn't hard-fail the request)."""

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


@pytest.fixture()
def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: its flush-on-teardown
    # keeps rate-limit buckets isolated across tests/files sharing this
    # session-scoped Redis, since TestClient always presents the same
    # fake caller IP.
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


async def test_register_login_logout_are_all_audit_logged(
    app_client: TestClient, db_bootstrap_pool: asyncpg.Pool
) -> None:
    register_resp = app_client.post(
        "/v1/auth/register",
        json={"email": "audited@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    assert register_resp.status_code == 201
    user_id = register_resp.json()["id"]

    login_resp = app_client.post(
        "/v1/auth/login", json={"email": "audited@example.com", "password": "s3cret!!"}
    )
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]

    logout_resp = app_client.post(
        "/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert logout_resp.status_code == 204

    async with db_bootstrap_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action, workspace_id, actor_user_id FROM audit_events "
            "WHERE actor_user_id = $1 ORDER BY occurred_at",
            uuid.UUID(user_id),
        )

    actions = [r["action"] for r in rows]
    assert actions == ["auth.user_registered", "auth.login_succeeded", "auth.logout"]
    assert all(r["workspace_id"] is None for r in rows), (
        "auth-plane events have no workspace — they must never be misattributed to one"
    )
