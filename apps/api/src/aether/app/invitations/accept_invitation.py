"""AcceptInvitation use case (FR-ID-3).

The one workspace-membership-creating flow where the tenant isn't known
upfront — the accepting user isn't a member yet, so there's nothing for
an HTTP-layer dependency to scope a connection to before this runs. The
composition layer resolves the connection's tenant scope by doing an
initial unvalidated lookup (see http/deps.py's
get_invitation_acceptance_scope); this use case does its own full
validation regardless of what that lookup found, so there is exactly one
place (here) that decides whether an invitation is acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.app.auth.tokens import hash_token
from aether.domain.entities import Membership
from aether.domain.errors import InvalidInvitationError
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import InvitationRepositoryPort, MembershipRepositoryPort
from aether.ports.security import ClockPort, IdPort


@dataclass(frozen=True, slots=True)
class AcceptInvitationCommand:
    raw_token: str
    accepting_user_id: UUID


class AcceptInvitation:
    def __init__(
        self,
        *,
        invitations: InvitationRepositoryPort,
        memberships: MembershipRepositoryPort,
        audit_log: AuditLogPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._invitations = invitations
        self._memberships = memberships
        self._audit_log = audit_log
        self._clock = clock
        self._ids = ids

    async def execute(self, command: AcceptInvitationCommand) -> Membership:
        invitation = await self._invitations.get_by_token_hash(hash_token(command.raw_token))
        # One error for unknown/expired/consumed — enumeration-safety
        # (see InvalidInvitationError's docstring).
        if (
            invitation is None
            or invitation.consumed_at is not None
            or invitation.expires_at <= self._clock.now()
        ):
            raise InvalidInvitationError("invalid or expired invitation")

        existing = await self._memberships.get(invitation.workspace_id, command.accepting_user_id)
        if existing is not None:
            # Already a member (e.g. double-click, or re-invited at the
            # same role) — accepting again is a harmless no-op, not an
            # error; consume the invitation so it can't be reused, but
            # don't create a duplicate membership row.
            await self._invitations.consume(invitation.id, consumed_at=self._clock.now())
            return existing

        membership = await self._memberships.create(
            id=self._ids.new_id(),
            workspace_id=invitation.workspace_id,
            user_id=command.accepting_user_id,
            role=invitation.role,
        )
        await self._invitations.consume(invitation.id, consumed_at=self._clock.now())
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=invitation.workspace_id,
            actor_user_id=command.accepting_user_id,
            actor_key_id=None,
            action="invitation.accepted",
            target_type="membership",
            target_id=membership.id,
            metadata={"role": invitation.role.value},
        )
        return membership
