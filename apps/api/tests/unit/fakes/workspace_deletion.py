from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from aether.domain.entities import DeletionJob, DeletionJobStatus


@dataclass
class RecordedCompletion:
    job_id: UUID
    workspace_id: UUID
    audit_event_id: UUID
    evidence: dict[str, Any]


class FakeWorkspaceDeletionRepository:
    """Worker-plane WorkspaceDeletionPort fake — separate seeding for
    jobs/object-keys from the API-plane FakeDeletionJobRepository since
    real deployments never share a single in-process store across the
    two roles either."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, DeletionJob] = {}
        self._object_keys: dict[UUID, list[str]] = {}
        self.hard_deleted_workspaces: list[UUID] = []
        self.completions: list[RecordedCompletion] = []

    def seed_job(self, job: DeletionJob) -> None:
        self._jobs[job.id] = job

    def seed_object_keys(self, workspace_id: UUID, keys: list[str]) -> None:
        self._object_keys[workspace_id] = keys

    async def get_job(self, workspace_id: UUID, job_id: UUID) -> DeletionJob | None:
        job = self._jobs.get(job_id)
        return job if job is not None and job.workspace_id == workspace_id else None

    async def mark_running(self, workspace_id: UUID, job_id: UUID) -> None:
        job = self._jobs.get(job_id)
        if (
            job is None
            or job.workspace_id != workspace_id
            or job.status != DeletionJobStatus.QUEUED
        ):
            return
        self._jobs[job_id] = replace(job, status=DeletionJobStatus.RUNNING)

    async def list_object_keys(self, workspace_id: UUID) -> list[str]:
        return list(self._object_keys.get(workspace_id, []))

    async def complete(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        audit_event_id: UUID,
        evidence: dict[str, Any],
        completed_at: object,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is not None and job.workspace_id == workspace_id:
            self._jobs[job_id] = replace(
                job,
                status=DeletionJobStatus.COMPLETE,
                evidence=evidence,
                completed_at=completed_at,  # type: ignore[arg-type]
            )
        self.hard_deleted_workspaces.append(workspace_id)
        self.completions.append(
            RecordedCompletion(
                job_id=job_id,
                workspace_id=workspace_id,
                audit_event_id=audit_event_id,
                evidence=evidence,
            )
        )
