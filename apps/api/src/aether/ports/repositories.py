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
from typing import Any, Protocol
from uuid import UUID

from aether.domain.entities import (
    AuditEvent,
    Invitation,
    Membership,
    MembershipRole,
    PasswordResetToken,
    RefreshToken,
    User,
    Workspace,
)
from aether.domain.errors import EmailAlreadyRegisteredError

__all__ = [
    "AuditEvent",
    "EmailAlreadyRegisteredError",
    "Invitation",
    "InvitationRepositoryPort",
    "Membership",
    "MembershipRepositoryPort",
    "MembershipRole",
    "PasswordResetToken",
    "PasswordResetTokenRepositoryPort",
    "RefreshToken",
    "RefreshTokenRepositoryPort",
    "User",
    "UserRepositoryPort",
    "Workspace",
    "WorkspaceRepositoryPort",
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


class WorkspaceRepositoryPort(Protocol):
    async def create(
        self,
        *,
        id: UUID,
        name: str,
        slug: str,
        settings: dict[str, Any],
        model_policy: dict[str, Any],
    ) -> Workspace: ...

    async def get_by_id(self, workspace_id: UUID) -> Workspace | None: ...

    async def update(
        self,
        workspace_id: UUID,
        *,
        name: str,
        settings: dict[str, Any],
        model_policy: dict[str, Any],
        expected_updated_at: datetime,
    ) -> Workspace | None:
        """Optimistic-concurrency update (ETag = ``updated_at``). Returns
        None if ``workspace_id`` doesn't exist *or* ``expected_updated_at``
        is stale — the caller (app layer) disambiguates via a follow-up
        existence check, since it already needs one to raise the right
        domain error (404 vs 409)."""
        ...

    async def soft_delete(self, workspace_id: UUID, *, deleted_at: datetime) -> None: ...


class MembershipRepositoryPort(Protocol):
    async def create(
        self, *, id: UUID, workspace_id: UUID, user_id: UUID, role: MembershipRole
    ) -> Membership: ...

    async def get(self, workspace_id: UUID, user_id: UUID) -> Membership | None: ...

    async def list_by_workspace(self, workspace_id: UUID) -> list[Membership]: ...

    async def update_role(
        self, workspace_id: UUID, user_id: UUID, *, role: MembershipRole
    ) -> Membership | None: ...

    async def delete(self, workspace_id: UUID, user_id: UUID) -> None: ...

    async def count_by_role(self, workspace_id: UUID, role: MembershipRole) -> int:
        """Used for last-owner protection: refuse a demote/remove that
        would take the Owner count to zero."""
        ...


class InvitationRepositoryPort(Protocol):
    async def create(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        email: str,
        role: MembershipRole,
        token_hash: str,
        invited_by: UUID,
        expires_at: datetime,
    ) -> Invitation: ...

    async def get_by_token_hash(self, token_hash: str) -> Invitation | None: ...

    async def consume(self, invitation_id: UUID, *, consumed_at: datetime) -> None: ...

    async def delete(self, workspace_id: UUID, invitation_id: UUID) -> None: ...


class PasswordResetTokenRepositoryPort(Protocol):
    async def create(
        self, *, id: UUID, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> PasswordResetToken: ...

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None: ...

    async def consume(self, token_id: UUID, *, consumed_at: datetime) -> None: ...
