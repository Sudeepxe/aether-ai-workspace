"""Real HTTP proof that security_headers_middleware (S10 #108, found by
a real ZAP baseline scan) actually sets its two headers on every
response — /healthz needs no live Postgres/Redis (it never touches
request.app.state.container), so this stays unit-tier.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aether.http.app import create_app

pytestmark = pytest.mark.unit


def test_responses_carry_the_two_headers_zap_flagged_as_missing() -> None:
    client = TestClient(create_app())

    resp = client.get("/healthz")

    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_headers_are_present_on_an_error_response_too() -> None:
    """Not just the happy path — the whole point is these headers apply
    uniformly, since the middleware wraps every response regardless of
    status code. A route that doesn't exist at all (no dependency
    resolution, no container needed) keeps this unit-tier."""
    client = TestClient(create_app())

    resp = client.get("/v1/this-route-does-not-exist")

    assert resp.status_code == 404
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cross-Origin-Resource-Policy"] == "same-origin"
