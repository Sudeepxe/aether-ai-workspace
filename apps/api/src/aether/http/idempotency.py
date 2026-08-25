"""Generic ``Idempotency-Key`` support for plain mutating POSTs (ADR-4.6,
§4.2's conventions table) — Stripe's model: a retried POST with the same
key and the same body replays the stored response instead of
re-executing; the same key with a *different* body is a 409, not a
silent apply-the-new-body.

Deliberately separate from the two existing narrower mechanisms (chat's
client-generated ``message_id``, document-confirm's document-row-is-
the-guard idempotency) — those solve a more specific problem than "replay
the exact prior HTTP response" (e.g. an SSE-streamed response has no
single "body" to snapshot) and aren't built on this mechanism; this one
is for the *other* mutating POSTs the spec always intended to cover
(workspace creation, invitations, API-key creation, ...).

Two cooperating pieces, opted into per route (not applied blanket to
every POST — auth/chat/document routes have their own semantics this
mechanism must not interfere with):

1. ``idempotency_guard`` — a FastAPI dependency added to a route's
   ``dependencies=[]``. No-ops if the client sent no ``Idempotency-Key``
   header (opt-in, per Stripe's model). Otherwise looks up any existing
   snapshot for this (identity, key): a body-hash match raises
   ``IdempotentReplay`` (short-circuits *before* the route body ever
   runs — this is what prevents the duplicate side effect, not a
   post-hoc check); a mismatch raises ``IdempotencyKeyConflictError``
   (-> 409 via the existing DomainError pipeline). A fresh key stashes
   ``(redis_key, body_sha256)`` on ``request.state`` for step 2.
2. ``IdempotencyAwareRoute`` — an ``APIRoute`` subclass a router opts
   into via ``APIRouter(route_class=IdempotencyAwareRoute)``. After the
   normal FastAPI response has been built (Pydantic-serialized,
   ``.body`` already concrete bytes — this layer is chosen specifically
   to avoid ASGI response-streaming reconstruction, which
   ``BaseHTTPMiddleware``-style generic middleware would require), it
   stores the snapshot if — and only if — step 1 set
   ``request.state.idempotency_pending`` *and* the response succeeded.
   A route that never uses ``idempotency_guard`` never sets that state,
   so wrapping a whole router with this class is a no-op for its other
   routes.

Only ``status_code`` + ``response_body`` are snapshotted (per ADR-4.6's
own "request-body hash + response" framing) — a replayed response does
not reproduce one-off headers a route may have set (e.g. workspace
creation's ``ETag``), a deliberately narrow scope match to what's
specified rather than an attempt to generalize further.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, Request, Response
from fastapi.routing import APIRoute

from aether.domain.errors import IdempotencyKeyConflictError
from aether.http.composition import Container
from aether.http.deps import AuthenticatedSession, get_container, get_current_session
from aether.ports.idempotency import IdempotencySnapshot

_TTL_SECONDS = 24 * 60 * 60


class IdempotentReplay(Exception):  # noqa: N818 — a control-flow signal, not an error: it
    # short-circuits the route via a *successful* stored response, not a failure.
    """Raised by ``idempotency_guard`` on a verified replay — caught by
    the dedicated handler registered in ``http/app.py`` (not a
    ``DomainError``: this is a successful-response replay, not a
    failure)."""

    def __init__(self, *, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body


async def idempotency_guard(
    request: Request,
    container: Container = Depends(get_container),
    session: AuthenticatedSession = Depends(get_current_session),
) -> None:
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return  # opt-in only, per Stripe's model — no header, no behavior change

    # workspace_id when the route has one in its path (invitations,
    # api-keys, ...); the caller's own identity otherwise (workspace
    # creation, which has no workspace_id yet). Matches the issue's own
    # "{workspace_or_user}" key-naming.
    workspace_id = request.path_params.get("workspace_id")
    identity = str(workspace_id) if workspace_id is not None else str(session.user_id)
    redis_key = f"idempotency:{identity}:{idempotency_key}"

    # Cached by Starlette on first read (self._body) — FastAPI's own
    # later Pydantic-body parsing reuses the same cached bytes, so
    # reading it here doesn't consume anything downstream needs.
    body = await request.body()
    body_sha256 = hashlib.sha256(body).hexdigest()

    existing = await container.idempotency_store.get(redis_key)
    if existing is not None:
        if existing.body_sha256 != body_sha256:
            raise IdempotencyKeyConflictError(idempotency_key)
        raise IdempotentReplay(status_code=existing.status_code, body=existing.response_body)

    request.state.idempotency_pending = (redis_key, body_sha256)


class IdempotencyAwareRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            response = await original_handler(request)
            pending: tuple[str, str] | None = getattr(request.state, "idempotency_pending", None)
            if pending is not None and 200 <= response.status_code < 300:
                redis_key, body_sha256 = pending
                container: Container = request.app.state.container
                await container.idempotency_store.set(
                    redis_key,
                    IdempotencySnapshot(
                        body_sha256=body_sha256,
                        status_code=response.status_code,
                        response_body=bytes(response.body).decode("utf-8"),
                    ),
                    ttl_seconds=_TTL_SECONDS,
                )
            return response

        return custom_handler
