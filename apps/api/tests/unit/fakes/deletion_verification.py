from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from aether.domain.entities import DeletionJob
from aether.ports.deletion_verification import ResidueReport


@dataclass
class RecordedVerification:
    job_id: UUID
    workspace_id: UUID
    report: ResidueReport
    audit_event_id: UUID


class FakeDeletionVerificationRepository:
    def __init__(self) -> None:
        self._jobs: dict[UUID, DeletionJob] = {}
        self._residue: dict[UUID, dict[str, int]] = {}
        self.recorded: list[RecordedVerification] = []

    def seed_job(self, job: DeletionJob) -> None:
        self._jobs[job.id] = job

    def seed_residue(self, workspace_id: UUID, residual_rows: dict[str, int]) -> None:
        self._residue[workspace_id] = residual_rows

    async def list_jobs_pending_verification(
        self, *, min_age_seconds: int, limit: int
    ) -> list[DeletionJob]:
        eligible = [
            job
            for job in self._jobs.values()
            if job.status.value == "complete" and job.verified_at is None
        ]
        eligible.sort(key=lambda j: j.completed_at or j.updated_at)
        return eligible[:limit]

    async def count_residual_rows(self, workspace_id: UUID) -> dict[str, int]:
        return dict(self._residue.get(workspace_id, {}))

    async def record_verification(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        report: ResidueReport,
        verified_at: object,
        audit_event_id: UUID,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            self._jobs[job_id] = replace(
                job,
                verified_at=verified_at,  # type: ignore[arg-type]
                verification_passed=report.passed,
            )
        self.recorded.append(
            RecordedVerification(
                job_id=job_id,
                workspace_id=workspace_id,
                report=report,
                audit_event_id=audit_event_id,
            )
        )
