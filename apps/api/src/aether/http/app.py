"""FastAPI application factory — composition root (Blueprint §3.9.1).

Sprint 0 shipped liveness/readiness only. Sprint 1 adds the auth module
(register/login/refresh/logout/me) and the machinery around it: a
lifespan-scoped Container (DB pool + Redis client + wired use cases,
composition.py), Problem-JSON-*shaped* (not yet the full RFC 9457
envelope — that's S2) error mapping for domain errors, and the
deny-by-default route-registration check (ADR-4.5) run at the end of
``create_app()`` so a route with no declared auth requirement is a boot
failure, not a runtime surprise.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from aether.config import get_settings
from aether.domain.errors import (
    AuthenticationFailedError,
    DomainError,
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
    RefreshTokenReusedError,
    UserNotFoundError,
)
from aether.http.authz import assert_all_routes_declare_auth
from aether.http.composition import build_container
from aether.http.routes.auth import router as auth_router
from aether.http.routes.me import router as me_router
from aether.logging import configure_logging, get_logger

REQUEST_ID_HEADER = "X-Request-ID"

log = get_logger(__name__)

_ERROR_STATUS: dict[type[DomainError], int] = {
    EmailAlreadyRegisteredError: 409,
    AuthenticationFailedError: 401,
    InvalidRefreshTokenError: 401,
    RefreshTokenReusedError: 401,
    InvalidAccessTokenError: 401,
    UserNotFoundError: 404,
}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    container = await build_container(settings)
    app.state.container = container
    try:
        yield
    finally:
        await container.aclose()


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
        lifespan=_lifespan,
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
        """Readiness: liveness plus the Sprint 1 dependencies actually
        being reachable (DB pool + Redis) — a stronger claim than S0's
        placeholder, which had none wired yet.
        """
        container = getattr(app.state, "container", None)
        if container is None:  # lifespan hasn't finished (or wasn't run, e.g. bare TestClient)
            return {"status": "degraded", "note": "dependencies not wired yet"}
        try:
            async with container.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            await container.redis_client.ping()
        except Exception:  # readiness must never 500, only report not-ready
            return {"status": "degraded"}
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(me_router)

    for error_type, status_code in _ERROR_STATUS.items():

        def _make_handler(code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
            # Registered per-exception-type below, so Starlette only ever
            # invokes this for that type (or a subclass) despite the
            # necessarily-wider `Exception` signature add_exception_handler
            # requires.
            async def _handler(request: Request, exc: Exception) -> JSONResponse:
                return JSONResponse(
                    status_code=code, content={"detail": str(exc) or exc.__class__.__name__}
                )

            return _handler

        app.add_exception_handler(error_type, _make_handler(status_code))

    assert_all_routes_declare_auth(app)  # ADR-4.5 — a boot failure, not a test-only check

    log.info("app_created", env=settings.env)
    return app
