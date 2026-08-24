from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from aether.ports.workspace_export import ExportJob, ExportJobStatus, WorkspaceExportData


@dataclass
class RecordedCompletion:
    job_id: UUID
    workspace_id: UUID
    archive_object_key: str
    evidence: dict[str, Any]


class FakeWorkspaceExportRepository:
    """Worker-plane WorkspaceExportPort fake — mirrors
    FakeWorkspaceDeletionRepository's shape (issue #84's precedent)."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, ExportJob] = {}
        self._export_data: dict[UUID, WorkspaceExportData] = {}
        self.completions: list[RecordedCompletion] = []

    def seed_job(self, job: ExportJob) -> None:
        self._jobs[job.id] = job

    def seed_export_data(self, workspace_id: UUID, data: WorkspaceExportData) -> None:
        self._export_data[workspace_id] = data

    async def get_job(self, workspace_id: UUID, job_id: UUID) -> ExportJob | None:
        job = self._jobs.get(job_id)
        return job if job is not None and job.workspace_id == workspace_id else None

    async def mark_running(self, workspace_id: UUID, job_id: UUID) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.workspace_id != workspace_id or job.status != ExportJobStatus.QUEUED:
            return
        self._jobs[job_id] = replace(job, status=ExportJobStatus.RUNNING)

    async def fetch_export_data(self, workspace_id: UUID) -> WorkspaceExportData:
        return self._export_data[workspace_id]

    async def complete(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        archive_object_key: str,
        evidence: dict[str, Any],
        completed_at: object,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is not None and job.workspace_id == workspace_id:
            self._jobs[job_id] = replace(
                job,
                status=ExportJobStatus.COMPLETE,
                archive_object_key=archive_object_key,
                evidence=evidence,
                completed_at=completed_at,  # type: ignore[arg-type]
            )
        self.completions.append(
            RecordedCompletion(
                job_id=job_id,
                workspace_id=workspace_id,
                archive_object_key=archive_object_key,
                evidence=evidence,
            )
        )
