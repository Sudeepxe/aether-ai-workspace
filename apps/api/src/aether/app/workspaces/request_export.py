"""RequestWorkspaceExport use case (§4.3: POST /workspaces/{ws}:export,
FR-AD-5). Mirrors DeleteWorkspace's job-creation shape (issue #84)
minus the soft-delete step — an export doesn't touch the workspace at
all, it only snapshots it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.audit import AuditLogPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.repositories import ExportJob, ExportJobRepositoryPort
from aether.ports.security import IdPort

WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE = "workspace.export_requested"


@dataclass(frozen=True, slots=True)
class RequestWorkspaceExportCommand:
    workspace_id: UUID
    actor_user_id: UUID


class RequestWorkspaceExport:
    def __init__(
        self,
        *,
        export_jobs: ExportJobRepositoryPort,
        outbox: OutboxRepositoryPort,
        audit_log: AuditLogPort,
        ids: IdPort,
    ) -> None:
        self._export_jobs = export_jobs
        self._outbox = outbox
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: RequestWorkspaceExportCommand) -> ExportJob:
        job = await self._export_jobs.create(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            requested_by=command.actor_user_id,
        )
        await self._outbox.enqueue(
            id=self._ids.new_id(),
            aggregate_type="workspace",
            aggregate_id=command.workspace_id,
            event_type=WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE,
            tenant_id=command.workspace_id,
            payload={"export_job_id": str(job.id)},
        )
        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="workspace.export_requested",
            target_type="workspace",
            target_id=command.workspace_id,
            metadata={"export_job_id": str(job.id)},
        )
        return job
