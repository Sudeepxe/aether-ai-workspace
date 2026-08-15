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
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import RefreshTokenRepositoryPort
from aether.ports.revocation import RevocationPort
from aether.ports.security import ClockPort, IdPort


@dataclass(frozen=True, slots=True)
class LogoutUserCommand:
    user_id: UUID
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
        audit_log: AuditLogPort,
        ids: IdPort,
    ) -> None:
        self._refresh_tokens = refresh_tokens
        self._revocations = revocations
        self._clock = clock
        self._audit_log = audit_log
        self._ids = ids

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

        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=None,
            actor_user_id=command.user_id,
            actor_key_id=None,
            action="auth.logout",
            target_type="user",
            target_id=command.user_id,
            metadata={},
        )
