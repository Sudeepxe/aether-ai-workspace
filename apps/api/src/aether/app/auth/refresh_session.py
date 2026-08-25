"""RefreshSession use case — rotation, family reuse-detection, grace window
(ADR-7.2, Ch.7 self-review F-2).

Three outcomes for a presented refresh token:

1. Unused, valid: rotate — mark it used, create a successor, return a new
   access token + the new raw refresh token.
2. Used, but within the 30s/same-device grace window: a benign race (the
   classic multi-tab double-refresh, Ch.5 F-1) — issue a fresh access
   token bound to the *existing* successor, without rotating again and
   without revoking. The refresh_token field of the result is None: the
   client's cookie should already hold the successor's value from the
   original response's Set-Cookie (ADR-7.1) — the raw successor value
   cannot be re-derived from its stored hash, so this path never invents
   a "second" successor that would orphan the first.
3. Used, outside the grace window or from a different device: theft
   signal — the entire token family is revoked and InvalidRefreshTokenError is
   raised (deliberately the same error the caller sees for "never
   existed"/"expired", so a probing client learns nothing extra).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from aether.app.auth.tokens import hash_refresh_token
from aether.domain.errors import InvalidRefreshTokenError, RefreshTokenReusedError
from aether.observability.metrics import AUTH_REFRESH_REUSE_TOTAL
from aether.ports.repositories import RefreshTokenRepositoryPort
from aether.ports.security import ClockPort, IdPort, TokenPort


@dataclass(frozen=True, slots=True)
class RefreshSessionCommand:
    raw_refresh_token: str
    device_fingerprint: str


@dataclass(frozen=True, slots=True)
class RefreshResult:
    user_id: UUID
    access_token: str
    refresh_token: str | None


class RefreshSession:
    def __init__(
        self,
        *,
        refresh_tokens: RefreshTokenRepositoryPort,
        tokens: TokenPort,
        clock: ClockPort,
        ids: IdPort,
        refresh_ttl_seconds: int,
        grace_seconds: int,
    ) -> None:
        self._refresh_tokens = refresh_tokens
        self._tokens = tokens
        self._clock = clock
        self._ids = ids
        self._refresh_ttl_seconds = refresh_ttl_seconds
        self._grace_seconds = grace_seconds

    async def execute(self, command: RefreshSessionCommand) -> RefreshResult:
        token = await self._refresh_tokens.get_by_hash(
            hash_refresh_token(command.raw_refresh_token)
        )
        now = self._clock.now()

        if token is None or token.revoked_at is not None or token.expires_at <= now:
            raise InvalidRefreshTokenError

        if token.used_at is None:
            raw_successor = secrets.token_urlsafe(32)
            successor_id = self._ids.new_id()
            await self._refresh_tokens.create(
                id=successor_id,
                user_id=token.user_id,
                family_id=token.family_id,
                token_hash=hash_refresh_token(raw_successor),
                device_fingerprint=command.device_fingerprint,
                expires_at=now + timedelta(seconds=self._refresh_ttl_seconds),
            )
            await self._refresh_tokens.mark_used(token.id, successor_id=successor_id, used_at=now)

            access_token = self._tokens.issue_access_token(
                user_id=token.user_id, jti=self._ids.new_id()
            )
            return RefreshResult(
                user_id=token.user_id, access_token=access_token, refresh_token=raw_successor
            )

        within_grace = (now - token.used_at) <= timedelta(seconds=self._grace_seconds)
        same_device = command.device_fingerprint == token.device_fingerprint
        if within_grace and same_device and token.successor_id is not None:
            access_token = self._tokens.issue_access_token(
                user_id=token.user_id, jti=self._ids.new_id()
            )
            return RefreshResult(
                user_id=token.user_id, access_token=access_token, refresh_token=None
            )

        # Reuse outside the grace window, or from a different device:
        # treat the family as compromised.
        await self._refresh_tokens.revoke_family(token.family_id, revoked_at=now)
        AUTH_REFRESH_REUSE_TOTAL.inc()
        raise RefreshTokenReusedError
