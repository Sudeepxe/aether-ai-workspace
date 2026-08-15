"""CreateInvitation use case (FR-ID-3). Role gating (Admin+) happens at
the HTTP layer via domain.policy before this executes.

The invitation email is enqueued via the transactional outbox, in the
same transaction as the invitation row itself (ADR-11.1: "all sends go
via the worker, queue-backed and retried") — the invitation can never
exist without its email also being queued to send, and vice versa.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from aether.app.auth.tokens import hash_token
from aether.app.notifications.dispatch_email_outbox import EMAIL_SEND_EVENT_TYPE
from aether.domain.entities import Invitation, MembershipRole
from aether.ports.audit import AuditLogPort
from aether.ports.outbox import OutboxRepositoryPort
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
        outbox: OutboxRepositoryPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._invitations = invitations
        self._audit_log = audit_log
        self._outbox = outbox
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
        await self._outbox.enqueue(
            id=self._ids.new_id(),
            aggregate_type="invitation",
            aggregate_id=invitation.id,
            event_type=EMAIL_SEND_EVENT_TYPE,
            tenant_id=command.workspace_id,
            payload={
                "to": command.email,
                "subject": "You've been invited to a workspace on Aether",
                # No frontend accept-page URL exists yet (S2 is
                # backend-only) — the raw token is included directly;
                # wiring a real link is a follow-up once that route exists.
                "text_body": f"You've been invited to join a workspace. Your invitation code: {raw_token}",
            },
        )
        return CreateInvitationResult(invitation=invitation, raw_token=raw_token)
