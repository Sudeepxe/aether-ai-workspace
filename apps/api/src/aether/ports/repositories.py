"""Repository ports (Blueprint §3.3 RepositoryPorts).

Protocol-based, not ABCs — app depends on these interfaces; adapters
(Postgres, or fakes in unit tests) implement them structurally.

Also re-exports the domain vocabulary these ports traffic in (User,
RefreshToken, EmailAlreadyRegisteredError): adapters are forbidden from
importing aether.domain directly (import-linter contract, "Adapters
depend only on ports"), so ports is the one doorway through which an
adapter reaches domain types it must construct or raise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from aether.domain.entities import RefreshToken, User
from aether.domain.errors import EmailAlreadyRegisteredError

__all__ = [
    "EmailAlreadyRegisteredError",
    "RefreshToken",
    "RefreshTokenRepositoryPort",
    "User",
    "UserRepositoryPort",
]


class UserRepositoryPort(Protocol):
    async def create(
        self, *, id: UUID, email: str, display_name: str, password_hash: str | None
    ) -> User: ...

    async def get_by_id(self, user_id: UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...


class RefreshTokenRepositoryPort(Protocol):
    async def create(
        self,
        *,
        id: UUID,
        user_id: UUID,
        family_id: UUID,
        token_hash: str,
        device_fingerprint: str,
        expires_at: datetime,
    ) -> RefreshToken: ...

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None: ...

    async def mark_used(self, token_id: UUID, *, successor_id: UUID, used_at: datetime) -> None: ...

    async def revoke_family(self, family_id: UUID, *, revoked_at: datetime) -> None: ...

    async def revoke_all_for_user(self, user_id: UUID, *, revoked_at: datetime) -> None: ...
