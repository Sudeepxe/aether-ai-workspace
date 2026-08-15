"""LogoutUser use case — immediate revocation for the current session.

Denies the presented access token's jti (so it stops working immediately,
not just at its natural ≤15-min expiry) and revokes the presented refresh
token's whole family (so it can't be used to mint further access tokens).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aether.app.auth.tokens import hash_refresh_token
from aether.ports.repositories import RefreshTokenRepositoryPort
from aether.ports.revocation import RevocationPort
from aether.ports.security import ClockPort


@dataclass(frozen=True, slots=True)
class LogoutUserCommand:
    jti: UUID
    access_token_expires_at: datetime
    raw_refresh_token: str | None


class LogoutUser:
    def __init__(
        self,
        *,
        refresh_tokens: RefreshTokenRepositoryPort,
        revocations: RevocationPort,
        clock: ClockPort,
    ) -> None:
        self._refresh_tokens = refresh_tokens
        self._revocations = revocations
        self._clock = clock

    async def execute(self, command: LogoutUserCommand) -> None:
        remaining = (command.access_token_expires_at - self._clock.now()).total_seconds()
        await self._revocations.deny(command.jti, ttl_seconds=max(int(remaining), 1))

        if command.raw_refresh_token is not None:
            token = await self._refresh_tokens.get_by_hash(
                hash_refresh_token(command.raw_refresh_token)
            )
            if token is not None:
                await self._refresh_tokens.revoke_family(
                    token.family_id, revoked_at=self._clock.now()
                )
