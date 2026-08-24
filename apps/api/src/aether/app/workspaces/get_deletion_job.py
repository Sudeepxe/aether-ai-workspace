"""GetDeletionJob use case (§4.3: GET /workspaces/{ws}/deletion-jobs/{id},
DF-3, mirrors documents' GetDocument status-polling precedent, issue #48)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.errors import DeletionJobNotFoundError
from aether.ports.repositories import DeletionJob, DeletionJobRepositoryPort


@dataclass(frozen=True, slots=True)
class GetDeletionJobCommand:
    workspace_id: UUID
    job_id: UUID


class GetDeletionJob:
    def __init__(self, *, deletion_jobs: DeletionJobRepositoryPort) -> None:
        self._deletion_jobs = deletion_jobs

    async def execute(self, command: GetDeletionJobCommand) -> DeletionJob:
        job = await self._deletion_jobs.get_by_id(command.workspace_id, command.job_id)
        if job is None:
            raise DeletionJobNotFoundError(str(command.job_id))
        return job
