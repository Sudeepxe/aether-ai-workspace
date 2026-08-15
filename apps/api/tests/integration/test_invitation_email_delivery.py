"""Proves issue #18's exact acceptance criterion: "invitation email
round-trips through mailpit in the dev profile" — against a real
mailpit container, not a mock. The dispatcher is invoked directly
(rather than running workers/main.py's poll loop and waiting on
wall-clock timing) — a worker dispatcher's correctness is proven by
calling the dispatch function and asserting on its effects.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from aether.adapters.clock import SystemClock
from aether.adapters.email.smtp import SmtpEmailAdapter
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.pool import create_pool
from aether.app.notifications.dispatch_email_outbox import DispatchEmailOutbox
from aether.config import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _as_role(bootstrap_url: str, role: str, password: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://{role}:{password}@{hostpart}"


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
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_role(postgres_url, "app_api", "app-api-dev-only"))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        get_settings.cache_clear()


async def test_invitation_email_round_trips_through_mailpit(
    app_client: TestClient,
    postgres_url: str,
    mailpit: tuple[str, int, str],
    mailpit_client: httpx.Client,
) -> None:
    owner_token_resp = app_client.post(
        "/v1/auth/register",
        json={"email": "owner@example.com", "password": "s3cret!!", "display_name": "Owner"},
    )
    assert owner_token_resp.status_code == 201
    login_resp = app_client.post(
        "/v1/auth/login", json={"email": "owner@example.com", "password": "s3cret!!"}
    )
    owner_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    ws_resp = app_client.post("/v1/workspaces", json={"name": "Acme"}, headers=owner_headers)
    workspace_id = ws_resp.json()["id"]

    invite_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/invitations",
        json={"email": "invitee@example.com", "role": "member"},
        headers=owner_headers,
    )
    assert invite_resp.status_code == 201

    # Not delivered yet — the HTTP request only enqueues the outbox row;
    # dispatch is the worker's job, invoked here directly.
    assert mailpit_client.get("/api/v1/messages").json()["messages_count"] == 0

    smtp_host, smtp_port, _ = mailpit
    worker_pool_url = _as_role(postgres_url, "app_worker", "app-worker-dev-only")
    # create_pool(), not a raw asyncpg.create_pool() call — it's what
    # registers the jsonb codec (adapters/postgres/pool.py); the real
    # worker process (workers/composition.py) always goes through it,
    # this test must too or it isn't actually exercising the real path.
    worker_pool = await create_pool(worker_pool_url)
    try:
        dispatcher = DispatchEmailOutbox(
            outbox=PostgresOutboxRepository(worker_pool),
            email=SmtpEmailAdapter(host=smtp_host, port=smtp_port, sender="noreply@aether.local"),
            clock=SystemClock(),
        )
        result = await dispatcher.execute()
    finally:
        await worker_pool.close()

    assert result.dispatched == 1
    assert result.failed == 0

    messages = mailpit_client.get("/api/v1/messages").json()
    assert messages["messages_count"] == 1
    message_summary = messages["messages"][0]
    assert message_summary["To"][0]["Address"] == "invitee@example.com"
    assert "invited" in message_summary["Subject"].lower()
