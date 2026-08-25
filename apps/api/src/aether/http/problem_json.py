"""RFC 9457 Problem+JSON error envelope (§3.6.1): ``{type, title, status,
detail, instance, correlation_id, code}`` on every non-2xx response.

Four handler classes are registered by ``install_error_handlers``:
- One per domain error type (the existing per-exception-type mapping,
  now producing the full envelope instead of a bare ``{"detail": ...}``).
- ``RequestValidationError`` (Pydantic/FastAPI request-body validation
  failures) -> 400, since those aren't domain errors but still need the
  same envelope shape, not FastAPI's default ``{"detail": [...]}``.
- ``HTTPException`` (FastAPI's own, e.g. authz.py's ``require_capability``
  403, or rate-limiting's 429) — FastAPI registers a default handler for
  this on every app that produces ``{"detail": ...}``; without overriding
  it here, every plain ``raise HTTPException(...)`` call site would
  silently bypass the envelope entirely, which is exactly what happened
  until this was added. Preserves any ``headers`` the exception carries
  (e.g. ``Retry-After``).
- A catch-all for any other ``Exception`` -> 500, whose ``detail`` is
  always the same generic string — an unhandled exception's message
  might contain internals (SQL, file paths, stack fragments) that must
  never cross the trust boundary (§3.6.1: "stack traces never cross
  TB-2"). The correlation ID is what lets an operator find the real
  detail in the server-side logs the caller never sees.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aether.domain.errors import DomainError
from aether.logging import get_logger
from aether.observability.metrics import RLS_VIOLATION_TOTAL

log = get_logger(__name__)

# Postgres SQLSTATE 42501 (insufficient_privilege) is the class RLS
# WITH CHECK failures raise ("new row violates row-level security
# policy for table ..."), but so are ordinary grant-missing errors —
# checking the message substring too keeps the page-grade "RLS
# violation (any)" alert (§10.4) from firing on an unrelated
# misconfigured-grant bug, which is a different (still real, but
# non-security) failure mode with its own remediation.
_RLS_VIOLATION_SQLSTATE = "42501"
_RLS_VIOLATION_MESSAGE_MARKER = "row-level security policy"

_PROBLEM_MEDIA_TYPE = "application/problem+json"
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

_STATUS_CODES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Bad Request"),
    401: ("unauthorized", "Unauthorized"),
    403: ("forbidden", "Forbidden"),
    404: ("not_found", "Not Found"),
    405: ("method_not_allowed", "Method Not Allowed"),
    409: ("conflict", "Conflict"),
    413: ("payload_too_large", "Payload Too Large"),
    422: ("unprocessable_entity", "Unprocessable Entity"),
    429: ("rate_limited", "Too Many Requests"),
}


def _code_and_title(exc_type: type[Exception]) -> tuple[str, str]:
    """Derives a stable machine-readable ``code`` and human ``title``
    from the exception class name, e.g. ``AuthenticationFailedError`` ->
    (``authentication_failed``, ``Authentication Failed``) — one source
    of truth per error type instead of a hand-maintained parallel table
    that drifts as errors are added."""
    name = exc_type.__name__.removesuffix("Error")
    words = _CAMEL_BOUNDARY.split(name)
    return "_".join(w.lower() for w in words), " ".join(words)


def _problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    correlation_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        media_type=_PROBLEM_MEDIA_TYPE,
        headers=headers,
        content={
            "type": f"urn:aether:error:{code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
            "correlation_id": correlation_id,
            "code": code,
        },
    )


def install_error_handlers(app: FastAPI, *, error_status: dict[type[DomainError], int]) -> None:
    for error_type, status_code in error_status.items():

        def _make_domain_handler(
            code_status: int, code_type: type[Exception]
        ) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
            # Registered per-exception-type below, so Starlette only ever
            # invokes this for that type (or a subclass) despite the
            # necessarily-wider `Exception` signature add_exception_handler
            # requires.
            code, title = _code_and_title(code_type)

            async def _handler(request: Request, exc: Exception) -> JSONResponse:
                return _problem_response(
                    request,
                    status_code=code_status,
                    code=code,
                    title=title,
                    detail=str(exc) or exc.__class__.__name__,
                )

            return _handler

        app.add_exception_handler(error_type, _make_domain_handler(status_code, error_type))

    async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)  # noqa: S101 — registered for this type only
        return _problem_response(
            request,
            status_code=status.HTTP_400_BAD_REQUEST,
            code="validation_error",
            title="Validation Error",
            detail="; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            ),
        )

    app.add_exception_handler(RequestValidationError, _validation_handler)

    async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPException)  # noqa: S101 — registered for this type only
        code, title = _STATUS_CODES.get(exc.status_code, ("http_error", "HTTP Error"))
        headers = dict(exc.headers) if exc.headers else None
        return _problem_response(
            request,
            status_code=exc.status_code,
            code=code,
            title=title,
            detail=str(exc.detail),
            headers=headers,
        )

    app.add_exception_handler(HTTPException, _http_exception_handler)

    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        if getattr(
            exc, "sqlstate", None
        ) == _RLS_VIOLATION_SQLSTATE and _RLS_VIOLATION_MESSAGE_MARKER in str(exc):
            # A real forced-RLS WITH CHECK rejection reaching the HTTP
            # layer at all is a "this should never happen" security
            # invariant violation (§10.4's page-grade alert) — normal
            # cross-tenant isolation is silent (empty result sets, see
            # §3.6.1's taxonomy note), so this only fires on a genuine
            # attempted policy violation, not routine denial.
            RLS_VIOLATION_TOTAL.inc()
        log.error(
            "unhandled_exception",
            exc_type=exc.__class__.__name__,
            path=request.url.path,
            correlation_id=getattr(request.state, "request_id", None),
        )
        return _problem_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_server_error",
            title="Internal Server Error",
            detail="An unexpected error occurred.",
        )

    app.add_exception_handler(Exception, _unhandled_handler)
