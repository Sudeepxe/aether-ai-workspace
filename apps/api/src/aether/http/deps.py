"""FastAPI dependency providers — the only place ``request.app.state`` is
touched, and the only place a Bearer header is turned into a verified
session (Blueprint ADR-7.1/7.2)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import Depends, Header, Request

from aether.domain.errors import InvalidAccessTokenError
from aether.http.composition import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user_id: UUID
    jti: UUID
    expires_at: datetime


async def get_current_session(
    container: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
) -> AuthenticatedSession:
    if authorization is None or not authorization.startswith("Bearer "):
        raise InvalidAccessTokenError("missing bearer token")
    token = authorization.removeprefix("Bearer ")

    claims = container.tokens.verify_access_token(token)  # raises InvalidAccessTokenError

    if await container.revocations.is_denied(claims.jti):
        raise InvalidAccessTokenError("token revoked")

    return AuthenticatedSession(user_id=claims.sub, jti=claims.jti, expires_at=claims.expires_at)


def device_fingerprint(user_agent: str | None = Header(default=None)) -> str:
    """A coarse, real (not fabricated) device signal: the User-Agent header,
    hashed to a fixed-length value for storage. ADR-7.2 calls for *a*
    per-device fingerprint without mandating its derivation; nothing in
    Sprint 1 needs finer-grained device identification than this."""
    raw = user_agent or "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
