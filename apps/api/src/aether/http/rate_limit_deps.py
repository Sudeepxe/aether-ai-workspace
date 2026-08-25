"""Rate-limit FastAPI dependencies (§3.6.3). App-tier limiting is by
identity: authenticated routes key by ``session.user_id``; public
pre-auth routes (register/login/refresh/password-reset) have no session
yet, so they key by the caller's address — a pragmatic identity for
routes with nothing better, not a duplicate of the edge tier's separate,
coarser per-IP anti-abuse pass (§3.6.3's "Edge: coarse per-IP" is a
different mechanism at a different layer; this app-tier bucket happens
to also look at IP only because no other identity exists pre-auth).

Per-workspace buckets are deliberately still deferred: no route needs
one yet. The per-API-key dimension (``rate_limit_by_user_or_api_key``)
exists alongside the per-user one (``rate_limit_by_user``) — §4.3's
``S,K`` routes (chat) accept either credential type on the same
Authorization header, so their rate limiter must too; a bare
``rate_limit_by_user`` on such a route would force ``get_current_session``
to reject any API-key caller before the route's own, API-key-aware
authorization dependency ever runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from fastapi import Depends, HTTPException, Request, status

from aether.app.api_keys.verify_api_key import ApiKeyPrincipal
from aether.http.composition import Container
from aether.http.deps import (
    AuthenticatedSession,
    get_container,
    get_current_session,
    get_session_or_api_key,
)


class RateLimitClass(StrEnum):
    AUTH = "auth"  # register, login, refresh, logout, password-reset, invitation accept
    CHEAP = "cheap"  # reads and routine mutations
    HEAVY = "heavy"  # LLM/upload — §4.3's resource catalog RL column


# (limit, window_seconds). "auth" is deliberately stricter — brute-force
# and credential-stuffing protection (§7.5 attack-surface review) is the
# whole point of that class existing separately from "cheap". "heavy" is
# stricter than "cheap" but far looser than "auth": it's abuse/DoS
# protection at the app tier, not budget enforcement — the Usage
# Metering service (§3.2.14, S4) is where real cost control lives.
_LIMITS: dict[RateLimitClass, tuple[int, int]] = {
    RateLimitClass.AUTH: (10, 60),
    RateLimitClass.CHEAP: (120, 60),
    RateLimitClass.HEAVY: (30, 60),
}


async def _enforce(
    *, request: Request, container: Container, rl_class: RateLimitClass, identity: str
) -> None:
    limit, window_seconds = _LIMITS[rl_class]
    key = f"ratelimit:{rl_class.value}:{identity}"
    result = await container.rate_limiter.check(key, limit=limit, window_seconds=window_seconds)
    # Stored on request.state, not set directly on an injected Response:
    # a Response mutated here is discarded whenever *anything* downstream
    # raises — not just this dependency's own 429, but any ordinary
    # domain error the route itself raises afterward (e.g. a 401 from
    # wrong credentials). request_id_middleware sits outside exception
    # handling and applies these headers to whatever response — success
    # or Problem+JSON error — actually goes out, uniformly.
    request.state.rate_limit = result
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded"
        )


def rate_limit_by_ip(rl_class: RateLimitClass) -> Callable[[Request, Container], Awaitable[None]]:
    async def _dependency(
        request: Request,
        container: Container = Depends(get_container),
    ) -> None:
        client = request.client
        identity = f"ip:{client.host}" if client is not None else "ip:unknown"
        await _enforce(request=request, container=container, rl_class=rl_class, identity=identity)

    return _dependency


def rate_limit_by_user(
    rl_class: RateLimitClass,
) -> Callable[[Request, Container, AuthenticatedSession], Awaitable[None]]:
    async def _dependency(
        request: Request,
        container: Container = Depends(get_container),
        session: AuthenticatedSession = Depends(get_current_session),
    ) -> None:
        await _enforce(
            request=request,
            container=container,
            rl_class=rl_class,
            identity=f"user:{session.user_id}",
        )

    return _dependency


def rate_limit_by_user_or_api_key(
    rl_class: RateLimitClass,
) -> Callable[[Request, Container, AuthenticatedSession | ApiKeyPrincipal], Awaitable[None]]:
    """The API-key-eligible counterpart to ``rate_limit_by_user`` — for
    routes that accept either credential type (§4.3's ``S,K`` column).
    A human session and a machine key never share a bucket even if
    presented for the same workspace: the key gets its own
    ``apikey:{id}`` identity, distinct from ``user:{id}``, since a
    machine integration's request volume shouldn't tighten (or loosen)
    the human owner's own budget."""

    async def _dependency(
        request: Request,
        container: Container = Depends(get_container),
        session_or_key: AuthenticatedSession | ApiKeyPrincipal = Depends(get_session_or_api_key),
    ) -> None:
        identity = (
            f"apikey:{session_or_key.api_key_id}"
            if isinstance(session_or_key, ApiKeyPrincipal)
            else f"user:{session_or_key.user_id}"
        )
        await _enforce(request=request, container=container, rl_class=rl_class, identity=identity)

    return _dependency
