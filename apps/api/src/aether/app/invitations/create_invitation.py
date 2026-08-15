"""CreateInvitation use case (FR-ID-3). Role gating (Admin+) happens at
the HTTP layer via domain.policy before this executes."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from aether.app.auth.tokens import hash_token
from aether.domain.entities import Invitation, MembershipRole
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import InvitationRepositoryPort
from aether.ports.security import ClockPort, IdPort

_EXPIRY = timedelta(days=7)  # §7.5 attack-surface review: "7-day expiry"


@dataclass(frozen=True, slots=True)
class CreateInvitationCommand:
    workspace_id: UUID
    email: str
    role: MembershipRole
    invited_by: UUID


@dataclass(frozen=True, slots=True)
class CreateInvitationResult:
    invitation: Invitation
    raw_token: str


class CreateInvitation:
    def __init__(
        self,
        *,
        invitations: InvitationRepositoryPort,
        audit_log: AuditLogPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._invitations = invitations
        self._audit_log = audit_log
        self._clock = clock
        self._ids = ids

    async def execute(self, command: CreateInvitationCommand) -> CreateInvitationResult:
        raw_token = secrets.token_urlsafe(16)  # 128 bits, §7.5
        invitation = await self._invitations.create(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            email=command.email,
            role=command.role,
            token_hash=hash_token(raw_token),
            invited_by=command.invited_by,
            expires_at=self._clock.now() + _EXPIRY,
        )
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.invited_by,
            actor_key_id=None,
            action="invitation.created",
            target_type="invitation",
            target_id=invitation.id,
            metadata={"email": command.email, "role": command.role.value},
        )
        return CreateInvitationResult(invitation=invitation, raw_token=raw_token)
