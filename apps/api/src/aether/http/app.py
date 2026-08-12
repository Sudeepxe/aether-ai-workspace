"""FastAPI application factory — Sprint 0 bootstrap skeleton.

Serves ONLY liveness/readiness endpoints (Blueprint §3.9.1). No business
routes exist yet; they arrive module-by-module from Sprint 1 with the
deny-by-default registration rule (ADR-4.5) enforced at that point.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response

from aether.config import get_settings
from aether.logging import configure_logging, get_logger

REQUEST_ID_HEADER = "X-Request-ID"

log = get_logger(__name__)


def create_app() -> FastAPI:
    """Build the application. Composition root for the HTTP process."""
    settings = get_settings()
    configure_logging(level=settings.log_level, service_name=settings.service_name)

    app = FastAPI(
        title="Aether AI Workspace API",
        version="0.1.0",
        # Spec is a generated, published artifact (ADR-9.2); docs routes stay
        # enabled in dev only.
        docs_url="/docs" if settings.env == "dev" else None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Bind a correlation ID to every request (Blueprint NFR-O-1).

        Honors an inbound X-Request-ID (edge-injected, §3.2.1) or mints one.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(correlation_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.get("/healthz", tags=["ops"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Liveness: process is up. Never checks dependencies (§3.9.1)."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], include_in_schema=False)
    async def readyz() -> dict[str, str]:
        """Readiness: will include dependency probes once adapters exist (S1+).

        Sprint 0: no dependencies are wired, so readiness == liveness.
        """
        return {"status": "ok", "note": "no dependencies wired yet (S0)"}

    log.info("app_created", env=settings.env)
    return app
