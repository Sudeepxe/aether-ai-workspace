"""RevokeInvitation use case (§4.3: DELETE /workspaces/{ws}/invitations/{id}).
Role gating (Admin+) happens at the HTTP layer."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.audit import AuditLogPort
from aether.ports.repositories import InvitationRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class RevokeInvitationCommand:
    workspace_id: UUID
    invitation_id: UUID
    actor_user_id: UUID


class RevokeInvitation:
    def __init__(
        self, *, invitations: InvitationRepositoryPort, audit_log: AuditLogPort, ids: IdPort
    ) -> None:
        self._invitations = invitations
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: RevokeInvitationCommand) -> None:
        await self._invitations.delete(command.workspace_id, command.invitation_id)
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="invitation.revoked",
            target_type="invitation",
            target_id=command.invitation_id,
            metadata={},
        )
