"""End-to-end HTTP proof of rate limiting (issue #23): a real over-limit
request sequence through the real app (real Redis-backed limiter, not a
fake) produces RateLimit-*/Retry-After headers and a 429 — not just that
the port's ``check()`` method returns the right dataclass in isolation.
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
    redis_client: redis_asyncio.Redis,  # unused directly: its flush-on-teardown
    # keeps each test's rate-limit buckets isolated, since these tests hit
    # the real Redis-backed limiter through the app rather than a fake.
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


async def test_login_route_enforces_the_auth_rate_limit_with_real_redis(
    app_client: TestClient,
) -> None:
    """RateLimitClass.AUTH is (10, 60) — see rate_limit_deps.py. The
    per-IP bucket is shared across register/login/refresh/etc within a
    class, so 10 requests against /v1/auth/login (whatever their
    outcome) exhaust it and the 11th must be denied."""
    body = {"email": "nobody@example.com", "password": "wrong-password"}

    responses = [app_client.post("/v1/auth/login", json=body) for _ in range(10)]
    for resp in responses:
        assert resp.status_code == 401  # unknown credentials, but not yet rate-limited
        assert resp.headers["RateLimit-Limit"] == "10"

    assert responses[0].headers["RateLimit-Remaining"] == "9"
    assert responses[-1].headers["RateLimit-Remaining"] == "0"

    denied = app_client.post("/v1/auth/login", json=body)

    assert denied.status_code == 429
    assert denied.headers["RateLimit-Limit"] == "10"
    assert denied.headers["RateLimit-Remaining"] == "0"
    assert int(denied.headers["Retry-After"]) > 0
    assert denied.headers["content-type"] == "application/problem+json"
    problem = denied.json()
    assert problem["status"] == 429
    assert problem["type"]
    assert "correlation_id" in problem


async def test_exhausting_the_by_ip_auth_bucket_does_not_lock_out_by_user_cheap_routes(
    app_client: TestClient,
) -> None:
    """GET /v1/me is CHEAP-class, keyed by user_id (rate_limit_by_user),
    not by IP — proves the bucket key is scoped by (class, identity)
    together, not shared just because requests share a caller IP."""
    register_resp = app_client.post(
        "/v1/auth/register",
        json={"email": "fresh-user@example.com", "password": "s3cret!!", "display_name": "F"},
    )
    assert register_resp.status_code == 201
    login_resp = app_client.post(
        "/v1/auth/login", json={"email": "fresh-user@example.com", "password": "s3cret!!"}
    )
    access_token = login_resp.json()["access_token"]

    for _ in range(10):
        app_client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    exhausted = app_client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
    )
    assert exhausted.status_code == 429

    me_resp = app_client.get("/v1/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200
    assert me_resp.headers["RateLimit-Limit"] == "120"
