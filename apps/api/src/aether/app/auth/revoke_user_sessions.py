"""RevokeUserSessions use case (FR-ID-7: admin terminates a user's sessions).

Revokes every refresh-token family for the user immediately, blocking all
future refresh. Already-issued access tokens are *not* individually
denylisted here — Sprint 1 has no index of every jti ever issued to a
user (adding one is real schema/complexity this sprint doesn't need), so
those ride out their own ≤15-minute expiry. This is the same bounded-
exposure shape the blueprint already accepts for the Redis-outage
fail-open case (ADR-3.6): revocation of *capability* is immediate, and
the worst-case exposure window is the short access-token TTL, not
unbounded.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.repositories import RefreshTokenRepositoryPort
from aether.ports.security import ClockPort


@dataclass(frozen=True, slots=True)
class RevokeUserSessionsCommand:
    user_id: UUID


class RevokeUserSessions:
    def __init__(self, *, refresh_tokens: RefreshTokenRepositoryPort, clock: ClockPort) -> None:
        self._refresh_tokens = refresh_tokens
        self._clock = clock

    async def execute(self, command: RevokeUserSessionsCommand) -> None:
        await self._refresh_tokens.revoke_all_for_user(
            command.user_id, revoked_at=self._clock.now()
        )
