"""DeleteWorkspace use case (§4.3: DELETE /workspaces/{ws}).

Synchronous soft-delete only for Sprint 2 — the blueprint's full "202,
cascade" async-job semantics needs worker/queue infrastructure that isn't
part of this sprint's scope (no S2 issue asks for it); building that
queue now, for one caller, would be exactly the premature infrastructure
the project's anti-over-engineering stance warns against. deleted_at is
set immediately and workspaces.get_by_id already filters it out, so the
row is functionally gone to every read path even though nothing has
cascaded yet — the cascade job is a tracked gap, not a silent omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.audit import AuditLogPort
from aether.ports.repositories import WorkspaceRepositoryPort
from aether.ports.security import ClockPort, IdPort


@dataclass(frozen=True, slots=True)
class DeleteWorkspaceCommand:
    workspace_id: UUID
    actor_user_id: UUID


class DeleteWorkspace:
    def __init__(
        self,
        *,
        workspaces: WorkspaceRepositoryPort,
        audit_log: AuditLogPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._workspaces = workspaces
        self._audit_log = audit_log
        self._clock = clock
        self._ids = ids

    async def execute(self, command: DeleteWorkspaceCommand) -> None:
        await self._workspaces.soft_delete(command.workspace_id, deleted_at=self._clock.now())
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="workspace.deleted",
            target_type="workspace",
            target_id=command.workspace_id,
            metadata={},
        )
