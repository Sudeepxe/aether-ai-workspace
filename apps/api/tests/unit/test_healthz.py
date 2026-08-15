"""Bootstrap wiring tests — prove the harness and the skeleton both work."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aether.http.app import REQUEST_ID_HEADER

pytestmark = pytest.mark.unit


def test_healthz_returns_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_without_lifespan_reports_degraded(client: TestClient) -> None:
    """The plain `client` fixture never runs the app's lifespan (Starlette
    only triggers it as a context manager), so app.state.container is
    unset — readyz must report this honestly (200 + degraded), not crash.
    The "ok when dependencies are actually reachable" case needs a real
    DB/Redis and lives in tests/integration/test_readyz.py."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_request_id_is_minted_when_absent(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert len(resp.headers[REQUEST_ID_HEADER]) == 32


def test_request_id_is_honoured_when_present(client: TestClient) -> None:
    """Edge-injected correlation IDs must survive the middleware (NFR-O-1)."""
    resp = client.get("/healthz", headers={REQUEST_ID_HEADER: "edge-supplied-id"})
    assert resp.headers[REQUEST_ID_HEADER] == "edge-supplied-id"
