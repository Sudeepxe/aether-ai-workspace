"""RegisterUser use case (FR-ID-1)."""

from __future__ import annotations

from dataclasses import dataclass

from aether.domain.entities import User
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
        ids: IdPort,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._ids = ids

    async def execute(self, command: RegisterUserCommand) -> User:
        # Raises EmailAlreadyRegisteredError (from the repository, at the
        # unique-constraint boundary) if the email is already taken.
        return await self._users.create(
            id=self._ids.new_id(),
            email=command.email,
            display_name=command.display_name,
            password_hash=self._hasher.hash(command.password),
        )
