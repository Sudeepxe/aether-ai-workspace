"""FastAPI application factory — composition root (Blueprint §3.9.1).

Sprint 0 shipped liveness/readiness only. Sprint 1 added the auth module
and a Problem-JSON-*shaped* (not yet the full RFC 9457 envelope) error
mapping for domain errors. Sprint 2 (problem_json.py) completes that
into the actual RFC 9457 envelope on every non-2xx response, and adds
the deny-by-default route-registration check (ADR-4.5) run at the end of
``create_app()`` so a route with no declared auth requirement is a boot
failure, not a runtime surprise.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response

from aether.config import get_settings
from aether.domain.errors import (
    AuthenticationFailedError,
    BudgetConcurrencyConflictError,
    BudgetExhaustedError,
    DocumentNotFoundError,
    DocumentUploadIncompleteError,
    DomainError,
    EmailAlreadyRegisteredError,
    FeedbackNotEligibleError,
    GenerationNotFoundError,
    InvalidAccessTokenError,
    InvalidInvitationError,
    InvalidPasswordResetTokenError,
    InvalidRefreshTokenError,
    LastOwnerProtectionError,
    MembershipNotFoundError,
    MessageNotFoundError,
    NoProviderAvailableError,
    RefreshTokenReusedError,
    ThreadNotFoundError,
    UserNotFoundError,
    WorkspaceConcurrencyConflictError,
    WorkspaceNotFoundError,
)
from aether.http.authz import assert_all_routes_declare_auth
from aether.http.composition import build_container
from aether.http.problem_json import install_error_handlers
from aether.http.routes.auth import router as auth_router
from aether.http.routes.documents import router as documents_router
from aether.http.routes.generations import router as generations_router
from aether.http.routes.invitations import router as invitations_router
from aether.http.routes.me import router as me_router
from aether.http.routes.messages import router as messages_router
from aether.http.routes.metering import router as metering_router
from aether.http.routes.threads import router as threads_router
from aether.http.routes.workspaces import router as workspaces_router
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
    WorkspaceNotFoundError: 404,
    MembershipNotFoundError: 404,
    LastOwnerProtectionError: 409,
    WorkspaceConcurrencyConflictError: 409,
    # One status for all three "unusable token" cases (unknown/expired/
    # consumed), matching the one-error-type enumeration-safety posture
    # already used for AuthenticationFailedError.
    InvalidInvitationError: 404,
    InvalidPasswordResetTokenError: 404,
    ThreadNotFoundError: 404,
    GenerationNotFoundError: 404,
    DocumentNotFoundError: 404,
    MessageNotFoundError: 404,
    # The client claims an upload finished; storage disagrees — a
    # precondition failure on the caller's own claimed state, not a
    # server error.
    DocumentUploadIncompleteError: 409,
    # The request is well-formed but targets a message that structurally
    # cannot receive feedback (a user's own turn, not the assistant's) —
    # an unprocessable semantic state, not a missing resource or a
    # conflicting write.
    FeedbackNotEligibleError: 422,
    # Refused before any provider call (§3.2.14) — a hard limit, not a
    # transient failure, but 429 (not 402/403) matches the rest of the
    # API's rate-limit vocabulary for "retry later, not your credentials".
    BudgetExhaustedError: 429,
    # Every provider in the fallback chain has an open circuit breaker
    # (§3.2.4) — a full outage of the router's dependencies, not a client
    # error.
    NoProviderAvailableError: 503,
    BudgetConcurrencyConflictError: 409,
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
        # Set explicitly on request.state (not just structlog's context)
        # so Problem+JSON error handlers can read it directly — relying
        # on structlog's contextvars propagation across the exception-
        # handling boundary is exactly the kind of implicit coupling that
        # breaks silently when middleware ordering changes.
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(correlation_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        # Applied here, not by the rate-limit dependency directly: a
        # Response object mutated inside a dependency is discarded
        # whenever anything downstream raises (the dependency's own 429,
        # or an ordinary domain error from the route itself), so headers
        # must be merged onto whatever response actually goes out —
        # after call_next(), which sits outside exception handling.
        rate_limit = getattr(request.state, "rate_limit", None)
        if rate_limit is not None:
            response.headers["RateLimit-Limit"] = str(rate_limit.limit)
            response.headers["RateLimit-Remaining"] = str(rate_limit.remaining)
            response.headers["RateLimit-Reset"] = str(rate_limit.reset_seconds)
            if not rate_limit.allowed:
                response.headers["Retry-After"] = str(rate_limit.reset_seconds)
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
    app.include_router(workspaces_router)
    app.include_router(invitations_router)
    app.include_router(threads_router)
    app.include_router(messages_router)
    app.include_router(generations_router)
    app.include_router(metering_router)
    app.include_router(documents_router)

    install_error_handlers(app, error_status=_ERROR_STATUS)

    assert_all_routes_declare_auth(app)  # ADR-4.5 — a boot failure, not a test-only check

    log.info("app_created", env=settings.env)
    return app
