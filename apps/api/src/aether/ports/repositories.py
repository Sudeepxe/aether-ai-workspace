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
    Message,
    MessageRole,
    MessageStatus,
    PasswordResetToken,
    RefreshToken,
    Thread,
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
    "Message",
    "MessageRepositoryPort",
    "MessageRole",
    "MessageStatus",
    "PasswordResetToken",
    "PasswordResetTokenRepositoryPort",
    "RefreshToken",
    "RefreshTokenRepositoryPort",
    "Thread",
    "ThreadRepositoryPort",
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

    async def update_password_hash(self, user_id: UUID, *, password_hash: str) -> None: ...


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


class ThreadRepositoryPort(Protocol):
    async def create(
        self, *, id: UUID, workspace_id: UUID, created_by: UUID, title: str | None
    ) -> Thread: ...

    async def get(self, workspace_id: UUID, thread_id: UUID) -> Thread | None: ...

    async def list_by_workspace(
        self, workspace_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Thread]:
        """Cursor pagination on (created_at DESC, id) per ADR-4.4, newest
        first; ``after`` is the (created_at, id) of the last item on the
        previous page, or None for the first page."""
        ...

    async def update_title(
        self, workspace_id: UUID, thread_id: UUID, *, title: str | None
    ) -> Thread | None: ...

    async def soft_delete(
        self, workspace_id: UUID, thread_id: UUID, *, deleted_at: datetime
    ) -> None: ...


class MessageRepositoryPort(Protocol):
    async def create(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        status: MessageStatus,
        client_message_id: str | None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_microcents: int | None = None,
        grounded: bool = False,
    ) -> Message:
        """Assigns ``seq`` transactionally via the locked per-thread
        counter row (ADR-8.2) — the caller never supplies it."""
        ...

    async def get_by_client_message_id(
        self, workspace_id: UUID, thread_id: UUID, client_message_id: str
    ) -> Message | None:
        """Idempotency lookup (ADR-4.6): a retried POST with the same
        client-generated ``message_id`` must not create a second message."""
        ...

    async def list_by_thread(
        self, workspace_id: UUID, thread_id: UUID, *, after_seq: int | None, limit: int
    ) -> list[Message]:
        """Cursor pagination on seq DESC (§8.1: seq is itself a natural,
        gapless pagination cursor — ADR-8.2)."""
        ...
