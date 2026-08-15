"""Membership management use cases (FR-ID-4, §4.3 Members resource).

ListMembers has no role gate beyond membership itself (any member can see
the roster). UpdateMemberRole and RemoveMember both enforce last-owner
protection here, in the domain-adjacent app layer — not at the HTTP
authz layer, which only knows "does this role have this capability", not
"would this specific action leave the workspace ownerless". That
invariant belongs with the operation it protects.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Membership, MembershipRole
from aether.domain.errors import LastOwnerProtectionError, MembershipNotFoundError
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import MembershipRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class ListMembersCommand:
    workspace_id: UUID


class ListMembers:
    def __init__(self, *, memberships: MembershipRepositoryPort) -> None:
        self._memberships = memberships

    async def execute(self, command: ListMembersCommand) -> list[Membership]:
        return await self._memberships.list_by_workspace(command.workspace_id)


@dataclass(frozen=True, slots=True)
class UpdateMemberRoleCommand:
    workspace_id: UUID
    target_user_id: UUID
    new_role: MembershipRole
    actor_user_id: UUID


class UpdateMemberRole:
    def __init__(
        self, *, memberships: MembershipRepositoryPort, audit_log: AuditLogPort, ids: IdPort
    ) -> None:
        self._memberships = memberships
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: UpdateMemberRoleCommand) -> Membership:
        current = await self._memberships.get(command.workspace_id, command.target_user_id)
        if current is None:
            raise MembershipNotFoundError(str(command.target_user_id))

        if current.role == MembershipRole.OWNER and command.new_role != MembershipRole.OWNER:
            owner_count = await self._memberships.count_by_role(
                command.workspace_id, MembershipRole.OWNER
            )
            if owner_count <= 1:
                raise LastOwnerProtectionError(str(command.workspace_id))

        updated = await self._memberships.update_role(
            command.workspace_id, command.target_user_id, role=command.new_role
        )
        assert updated is not None  # noqa: S101 — current was just read inside this same scope
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="membership.role_changed",
            target_type="membership",
            target_id=command.target_user_id,
            metadata={"old_role": current.role.value, "new_role": command.new_role.value},
        )
        return updated


@dataclass(frozen=True, slots=True)
class RemoveMemberCommand:
    workspace_id: UUID
    target_user_id: UUID
    actor_user_id: UUID


class RemoveMember:
    def __init__(
        self, *, memberships: MembershipRepositoryPort, audit_log: AuditLogPort, ids: IdPort
    ) -> None:
        self._memberships = memberships
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: RemoveMemberCommand) -> None:
        current = await self._memberships.get(command.workspace_id, command.target_user_id)
        if current is None:
            raise MembershipNotFoundError(str(command.target_user_id))

        if current.role == MembershipRole.OWNER:
            owner_count = await self._memberships.count_by_role(
                command.workspace_id, MembershipRole.OWNER
            )
            if owner_count <= 1:
                raise LastOwnerProtectionError(str(command.workspace_id))

        await self._memberships.delete(command.workspace_id, command.target_user_id)
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="membership.removed",
            target_type="membership",
            target_id=command.target_user_id,
            metadata={"role": current.role.value},
        )
