"""VerifyWorkspaceDeletions — the scheduled sweep over completed
``deletion_jobs`` (NFR-PR-1, §11.6's S8 exit criterion, issue #86).

Not outbox-driven, unlike every other worker-plane dispatcher in this
codebase (email, ingestion relay, workspace deletion, workspace
export) — there's no natural event to enqueue for "verify sometime
after completion" (the deletion saga itself has already finished by
the time this needs to run). A scheduled sweep over ``deletion_jobs``
rows old enough to check is the mechanism instead: this dispatcher is
just as safe to call repeatedly as the outbox-driven ones (a job
already verified is never re-verified — ``list_jobs_pending_verification``
excludes it), it just polls a different source of pending work.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from aether.ports.deletion_verification import DeletionJob, DeletionVerificationPort, ResidueReport
from aether.ports.security import ClockPort, IdPort
from aether.ports.storage import ObjectStoragePort

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: int
    failed: int


class VerifyWorkspaceDeletions:
    def __init__(
        self,
        *,
        repository: DeletionVerificationPort,
        object_storage: ObjectStoragePort,
        clock: ClockPort,
        ids: IdPort,
        min_age_seconds: int,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._clock = clock
        self._ids = ids
        self._min_age_seconds = min_age_seconds

    async def execute(self, *, batch_size: int = 20) -> VerificationResult:
        """The scheduled sweep: every completed, not-yet-verified job
        old enough to check. A job already verified (pass or fail) is
        never picked up again here — a persistently-failing job is a
        page-the-humans situation, not something to burn cycles
        re-checking every cycle forever; ``verify_job`` below is the
        deliberate, explicit re-check path once a human has acted."""
        jobs = await self._repository.list_jobs_pending_verification(
            min_age_seconds=self._min_age_seconds, limit=batch_size
        )
        passed = 0
        failed = 0
        for job in jobs:
            report = await self.verify_job(job)
            if report.passed:
                passed += 1
            else:
                failed += 1
        return VerificationResult(passed=passed, failed=failed)

    async def verify_job(self, job: DeletionJob) -> ResidueReport:
        """Standalone, worker-triggerable re-check for a specific,
        already-known job — the same real, independent residue sweep
        ``execute``'s loop runs, callable directly (ops re-running
        verification after fixing a real gap it found; tests proving
        the verifier genuinely detects residue rather than rubber-
        stamping). Always re-records the result, including on a job
        that was already verified before."""
        report = await self._verify_one(job.workspace_id)
        await self._repository.record_verification(
            job_id=job.id,
            workspace_id=job.workspace_id,
            report=report,
            verified_at=self._clock.now(),
            audit_event_id=self._ids.new_id(),
        )
        if not report.passed:
            log.error(
                "deletion_verification_failed",
                workspace_id=str(job.workspace_id),
                deletion_job_id=str(job.id),
                residual_rows=report.residual_rows,
                residual_object_count=len(report.residual_object_keys),
            )
        return report

    async def _verify_one(self, workspace_id: UUID) -> ResidueReport:
        residual_rows = await self._repository.count_residual_rows(workspace_id)
        residual_object_keys = await self._object_storage.list_prefix(prefix=f"{workspace_id}/")
        return ResidueReport(residual_rows=residual_rows, residual_object_keys=residual_object_keys)
