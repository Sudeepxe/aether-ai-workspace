"""RegisterUser use case (FR-ID-1)."""

from __future__ import annotations

from dataclasses import dataclass

from aether.domain.entities import User
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import UserRepositoryPort
from aether.ports.security import IdPort, PasswordHasherPort


@dataclass(frozen=True, slots=True)
class RegisterUserCommand:
    email: str
    password: str
    display_name: str


class RegisterUser:
    def __init__(
        self,
        *,
        users: UserRepositoryPort,
        hasher: PasswordHasherPort,
        audit_log: AuditLogPort,
        ids: IdPort,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: RegisterUserCommand) -> User:
        # Raises EmailAlreadyRegisteredError (from the repository, at the
        # unique-constraint boundary) if the email is already taken.
        user = await self._users.create(
            id=self._ids.new_id(),
            email=command.email,
            display_name=command.display_name,
            password_hash=self._hasher.hash(command.password),
        )
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=None,
            actor_user_id=user.id,
            actor_key_id=None,
            action="auth.user_registered",
            target_type="user",
            target_id=user.id,
            metadata={},
        )
        return user
