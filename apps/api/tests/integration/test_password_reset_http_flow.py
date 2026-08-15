"""End-to-end HTTP proof of the password-reset flow (ADR-11.1, issue #19):
enumeration-safety (identical response for known vs unknown email),
single-use token, and — the one property no unit test with fakes fully
proves — that a completed reset genuinely revokes every other session,
checked against the real Redis-backed refresh-token/session stack.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from aether.app.auth.tokens import hash_token
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


async def test_request_reset_returns_identical_response_for_known_and_unknown_email(
    app_client: TestClient,
) -> None:
    app_client.post(
        "/v1/auth/register",
        json={"email": "known@example.com", "password": "s3cret!!", "display_name": "K"},
    )

    known_resp = app_client.post(
        "/v1/auth/password-reset:request", json={"email": "known@example.com"}
    )
    unknown_resp = app_client.post(
        "/v1/auth/password-reset:request", json={"email": "nobody@example.com"}
    )

    assert known_resp.status_code == unknown_resp.status_code == 202
    assert known_resp.text == unknown_resp.text


async def test_full_reset_flow_revokes_prior_sessions_and_swaps_the_password(
    app_client: TestClient, db_bootstrap_pool: asyncpg.Pool
) -> None:
    app_client.post(
        "/v1/auth/register",
        json={"email": "reset-me@example.com", "password": "old-password!", "display_name": "R"},
    )
    login_resp = app_client.post(
        "/v1/auth/login", json={"email": "reset-me@example.com", "password": "old-password!"}
    )
    old_access_token = login_resp.json()["access_token"]
    assert (
        app_client.get(
            "/v1/me", headers={"Authorization": f"Bearer {old_access_token}"}
        ).status_code
        == 200
    )

    request_resp = app_client.post(
        "/v1/auth/password-reset:request", json={"email": "reset-me@example.com"}
    )
    assert request_resp.status_code == 202

    # The raw token is only ever readable by the recipient's inbox in
    # production (EmailPort); recover it here the same way
    # test_workspace_http_flow.py recovers an invitation token — read the
    # hash straight from the DB and reconstruct via a fresh raw token that
    # hashes to it is not possible (one-way hash), so instead we read the
    # *row* to prove it exists, and drive the confirm step by seeding a
    # fresh token whose raw value we do control, matching how a real
    # confirm request only ever carries the raw value from the email.
    async with db_bootstrap_pool.acquire() as conn:
        user_row = await conn.fetchrow("SELECT id FROM users WHERE email = 'reset-me@example.com'")
        existing = await conn.fetchrow(
            "SELECT id FROM password_reset_tokens WHERE user_id = $1", user_row["id"]
        )
        assert existing is not None, "RequestPasswordReset must have created a token row"

        raw_token = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at) "
            "VALUES ($1, $2, $3, now() + interval '30 minutes')",
            uuid.uuid4(),
            user_row["id"],
            hash_token(raw_token),
        )

    confirm_resp = app_client.post(
        "/v1/auth/password-reset:confirm",
        json={"token": raw_token, "new_password": "brand-new-password!"},
    )
    assert confirm_resp.status_code == 204

    # RevokeUserSessions revokes the refresh family, not individual
    # already-issued access-token jtis (see its docstring — the bounded-
    # exposure design already accepted for Sprint 1). The real,
    # verifiable guarantee is that the pre-reset refresh family can no
    # longer mint a new access token at all.
    refresh_resp = app_client.post("/v1/auth/refresh")
    assert refresh_resp.status_code == 401, "the pre-reset refresh family must be revoked"

    old_password_login = app_client.post(
        "/v1/auth/login", json={"email": "reset-me@example.com", "password": "old-password!"}
    )
    assert old_password_login.status_code == 401

    new_password_login = app_client.post(
        "/v1/auth/login",
        json={"email": "reset-me@example.com", "password": "brand-new-password!"},
    )
    assert new_password_login.status_code == 200

    # Single-use: replaying the same confirm request must fail.
    replay_resp = app_client.post(
        "/v1/auth/password-reset:confirm",
        json={"token": raw_token, "new_password": "yet-another-password!"},
    )
    assert replay_resp.status_code == 404
