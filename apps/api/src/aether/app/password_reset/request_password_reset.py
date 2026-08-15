"""RequestPasswordReset use case (ADR-11.1).

Enumeration-safe: the HTTP layer returns the identical response whether
or not the email belongs to a real account (a generic "if that email has
an account, a reset link was sent"). This use case is what makes that
true rather than merely claimed — it does nothing observable (no token
row, no queued email) when the user doesn't exist, and callers can't
distinguish that from "the email send is merely slow", since the route
never reports success/failure of the send itself either way.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from aether.app.auth.tokens import hash_token
from aether.app.notifications.dispatch_email_outbox import EMAIL_SEND_EVENT_TYPE
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.repositories import PasswordResetTokenRepositoryPort, UserRepositoryPort
from aether.ports.security import ClockPort, IdPort

_EXPIRY = timedelta(minutes=30)  # ADR-11.1: "30-minute TTL"


@dataclass(frozen=True, slots=True)
class RequestPasswordResetCommand:
    email: str


class RequestPasswordReset:
    def __init__(
        self,
        *,
        users: UserRepositoryPort,
        password_reset_tokens: PasswordResetTokenRepositoryPort,
        outbox: OutboxRepositoryPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._users = users
        self._password_reset_tokens = password_reset_tokens
        self._outbox = outbox
        self._clock = clock
        self._ids = ids

    async def execute(self, command: RequestPasswordResetCommand) -> None:
        user = await self._users.get_by_email(command.email)
        if user is None or user.password_hash is None:
            # OAuth-only accounts (password_hash is None) have no
            # password to reset — silently no-op, same as "no such
            # user", for the identical enumeration-safety reason.
            return

        raw_token = secrets.token_urlsafe(16)  # 128 bits, matching invitations' token strength
        token = await self._password_reset_tokens.create(
            id=self._ids.new_id(),
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=self._clock.now() + _EXPIRY,
        )
        await self._outbox.enqueue(
            id=self._ids.new_id(),
            aggregate_type="password_reset_token",
            aggregate_id=token.id,
            event_type=EMAIL_SEND_EVENT_TYPE,
            tenant_id=None,
            payload={
                "to": user.email,
                "subject": "Reset your Aether password",
                "text_body": (
                    "A password reset was requested for your account. "
                    f"Your reset code: {raw_token} (expires in 30 minutes). "
                    "If you didn't request this, you can ignore this email."
                ),
            },
        )
