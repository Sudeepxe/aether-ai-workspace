"""LoginUser use case (FR-ID-1, §7.4/§7.5 enumeration-safety).

"Unknown email" and "wrong password" are indistinguishable to the caller
— both raise AuthenticationFailedError — and take approximately the same time:
when the email doesn't exist, a verify still runs against a fixed dummy
hash, so a timing side-channel can't be used to enumerate accounts.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from aether.app.auth.tokens import hash_refresh_token
from aether.domain.errors import AuthenticationFailedError
from aether.ports.repositories import RefreshTokenRepositoryPort, UserRepositoryPort
from aether.ports.security import ClockPort, IdPort, PasswordHasherPort, TokenPort

_DUMMY_PASSWORD = "aether-enumeration-safety-dummy"  # noqa: S105 — not a credential, a fixed timing-parity input


@dataclass(frozen=True, slots=True)
class LoginUserCommand:
    email: str
    password: str
    device_fingerprint: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    user_id: UUID
    access_token: str
    refresh_token: str


class LoginUser:
    def __init__(
        self,
        *,
        users: UserRepositoryPort,
        refresh_tokens: RefreshTokenRepositoryPort,
        hasher: PasswordHasherPort,
        tokens: TokenPort,
        clock: ClockPort,
        ids: IdPort,
        refresh_ttl_seconds: int,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._ids = ids
        self._refresh_ttl_seconds = refresh_ttl_seconds
        # Computed once (not per request) so the dummy-verify path has a
        # real hash to check against for timing parity (see module docstring).
        self._dummy_hash = hasher.hash(_DUMMY_PASSWORD)

    async def execute(self, command: LoginUserCommand) -> LoginResult:
        user = await self._users.get_by_email(command.email)
        if user is None or user.password_hash is None:
            self._hasher.verify(command.password, self._dummy_hash)
            raise AuthenticationFailedError
        if not self._hasher.verify(command.password, user.password_hash):
            raise AuthenticationFailedError

        jti = self._ids.new_id()
        access_token = self._tokens.issue_access_token(user_id=user.id, jti=jti)

        raw_refresh_token = secrets.token_urlsafe(32)
        now = self._clock.now()
        await self._refresh_tokens.create(
            id=self._ids.new_id(),
            user_id=user.id,
            family_id=self._ids.new_id(),
            token_hash=hash_refresh_token(raw_refresh_token),
            device_fingerprint=command.device_fingerprint,
            expires_at=now + timedelta(seconds=self._refresh_ttl_seconds),
        )

        return LoginResult(
            user_id=user.id, access_token=access_token, refresh_token=raw_refresh_token
        )
