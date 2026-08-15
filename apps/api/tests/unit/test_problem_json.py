"""Proves issue #22's acceptance criterion: every non-2xx response
conforms to the RFC 9457 Problem+JSON schema (§3.6.1) — not just that
domain errors map to the right status code (already covered elsewhere),
but that the actual response body has the required shape, the right
content type, and that unhandled exceptions never leak internals.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aether.http.authz import AuthRequirement, route_auth
from aether.http.problem_json import install_error_handlers

pytestmark = pytest.mark.unit

_REQUIRED_FIELDS = {"type", "title", "status", "detail", "instance", "correlation_id", "code"}


def test_domain_error_response_has_the_full_problem_json_shape(http_client: TestClient) -> None:
    payload = {"email": "a@example.com", "password": "s3cret!!", "display_name": "A"}
    http_client.post("/v1/auth/register", json=payload)
    resp = http_client.post("/v1/auth/register", json=payload)  # duplicate -> 409

    assert resp.status_code == 409
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert _REQUIRED_FIELDS <= body.keys()
    assert body["status"] == 409
    assert body["code"] == "email_already_registered"
    assert body["title"] == "Email Already Registered"
    assert body["instance"] == "/v1/auth/register"
    # The correlation ID in the body must be the same one the client can
    # already see on the response header (§3.6.1: "correlation ID always
    # crosses" the trust boundary) — not two independently-generated ids.
    assert body["correlation_id"] == resp.headers["x-request-id"]


def test_validation_error_uses_problem_json_not_fastapis_default_shape(
    http_client: TestClient,
) -> None:
    # Missing required fields entirely -> FastAPI/Pydantic validation
    # failure, not a domain error — must still get the same envelope.
    resp = http_client.post("/v1/auth/register", json={})

    assert resp.status_code == 400
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert _REQUIRED_FIELDS <= body.keys()
    assert body["code"] == "validation_error"


def test_unhandled_exception_returns_500_with_no_leaked_internals() -> None:
    """A synthetic app + a route that raises a plain, undeclared
    exception — the catch-all handler must produce the same envelope
    shape and, critically, never surface the real exception message
    (which could contain SQL, file paths, or other internals)."""
    app = FastAPI()

    @app.get("/boom", openapi_extra=route_auth(AuthRequirement.PUBLIC))
    async def boom() -> None:
        raise RuntimeError("super secret internal detail — connection string, stack frame, etc.")

    install_error_handlers(app, error_status={})
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert _REQUIRED_FIELDS <= body.keys()
    assert body["code"] == "internal_server_error"
    assert "super secret internal detail" not in resp.text
