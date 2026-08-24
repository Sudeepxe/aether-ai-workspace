"""DeleteWorkspace use case (§4.3: DELETE /workspaces/{ws}, DF-3).

Real async cascade (issue #84), replacing the Sprint 2 synchronous-
soft-delete-only stub: ``deleted_at`` is still set immediately in this
same request transaction (workspaces.get_by_id already filters it out,
so the workspace is functionally gone to every read path the instant
this call returns), but the actual cascade — object-storage purge, then
the hard-delete that cascades every child table — now runs as a real
worker-plane saga, matching documents' already-real async pattern
(issue #48) and §4.3's documented "202, cascade" resource catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.audit import AuditLogPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.repositories import (
    DeletionJob,
    DeletionJobRepositoryPort,
    WorkspaceRepositoryPort,
)
from aether.ports.security import ClockPort, IdPort

WORKSPACE_DELETE_REQUESTED_EVENT_TYPE = "workspace.delete_requested"


@dataclass(frozen=True, slots=True)
class DeleteWorkspaceCommand:
    workspace_id: UUID
    actor_user_id: UUID


class DeleteWorkspace:
    def __init__(
        self,
        *,
        workspaces: WorkspaceRepositoryPort,
        deletion_jobs: DeletionJobRepositoryPort,
        outbox: OutboxRepositoryPort,
        audit_log: AuditLogPort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._workspaces = workspaces
        self._deletion_jobs = deletion_jobs
        self._outbox = outbox
        self._audit_log = audit_log
        self._clock = clock
        self._ids = ids

    async def execute(self, command: DeleteWorkspaceCommand) -> DeletionJob:
        await self._workspaces.soft_delete(command.workspace_id, deleted_at=self._clock.now())
        job = await self._deletion_jobs.create(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            requested_by=command.actor_user_id,
        )
        await self._outbox.enqueue(
            id=self._ids.new_id(),
            aggregate_type="workspace",
            aggregate_id=command.workspace_id,
            event_type=WORKSPACE_DELETE_REQUESTED_EVENT_TYPE,
            tenant_id=command.workspace_id,
            payload={"deletion_job_id": str(job.id)},
        )
        # This is the request-time marker, not the saga's own completion
        # evidence (that's a separate, worker-written workspace.deleted
        # system-plane event once the cascade actually finishes — see
        # ports.workspace_deletion's docstring). Kept here, unchanged
        # from the Sprint 2 stub, since it's still meaningful signal:
        # "a deletion was requested, by whom, when" independent of
        # whether the saga has completed yet.
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="workspace.delete_requested",
            target_type="workspace",
            target_id=command.workspace_id,
            metadata={"deletion_job_id": str(job.id)},
        )
        return job
