"""ResetPassword use case (ADR-11.1): consumes a single-use reset token,
sets the new password, and revokes every active session/refresh family —
a successful reset ends every other session too, not just this request's
(if the token leaked, so did the attacker's window; ending everything
bounds it to zero)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.app.auth.revoke_user_sessions import RevokeUserSessions, RevokeUserSessionsCommand
from aether.app.auth.tokens import hash_token
from aether.domain.errors import InvalidPasswordResetTokenError
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import PasswordResetTokenRepositoryPort, UserRepositoryPort
from aether.ports.security import ClockPort, IdPort, PasswordHasherPort


@dataclass(frozen=True, slots=True)
class ResetPasswordCommand:
    raw_token: str
    new_password: str


class ResetPassword:
    def __init__(
        self,
        *,
        users: UserRepositoryPort,
        password_reset_tokens: PasswordResetTokenRepositoryPort,
        hasher: PasswordHasherPort,
        revoke_user_sessions: RevokeUserSessions,
        audit_log: AuditLogPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._users = users
        self._password_reset_tokens = password_reset_tokens
        self._hasher = hasher
        self._revoke_user_sessions = revoke_user_sessions
        self._audit_log = audit_log
        self._clock = clock
        self._ids = ids

    async def execute(self, command: ResetPasswordCommand) -> UUID:
        token = await self._password_reset_tokens.get_by_token_hash(hash_token(command.raw_token))
        # One error for unknown/expired/consumed — enumeration-safety,
        # identical posture to InvalidInvitationError.
        if token is None or token.consumed_at is not None or token.expires_at <= self._clock.now():
            raise InvalidPasswordResetTokenError("invalid or expired reset token")

        await self._users.update_password_hash(
            token.user_id, password_hash=self._hasher.hash(command.new_password)
        )
        await self._password_reset_tokens.consume(token.id, consumed_at=self._clock.now())
        await self._revoke_user_sessions.execute(RevokeUserSessionsCommand(user_id=token.user_id))
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=None,
            actor_user_id=token.user_id,
            actor_key_id=None,
            action="auth.password_reset_completed",
            target_type="user",
            target_id=token.user_id,
            metadata={},
        )
        return token.user_id
