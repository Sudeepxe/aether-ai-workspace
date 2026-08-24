"""Worker-plane port for the deletion-verification saga (NFR-PR-1,
§11.6's S8 exit criterion, issue #86).

Pool-bound, mirroring the other workspace-saga worker-plane ports
(``workspace_deletion``, ``workspace_export``). Deliberately NOT
outbox-driven — there's no natural event to enqueue for "verify
sometime after completion"; a scheduled sweep over ``deletion_jobs``
itself (``status='complete'``, ``verified_at IS NULL``, old enough) is
the mechanism, matching the issue's own "a scheduled sweep... not
immediately inline" framing.

This is the one saga in the whole deletion/export family whose entire
purpose is to NOT trust the other sagas' own bookkeeping — every count
here must come from a real, independent query against the actual
table, never from ``deletion_jobs.evidence`` or any other value the
deletion saga itself already computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from aether.domain.entities import DeletionJob, DeletionJobStatus

__all__ = ["DeletionJob", "DeletionJobStatus", "DeletionVerificationPort", "ResidueReport"]


@dataclass(frozen=True, slots=True)
class ResidueReport:
    """``residual_rows`` only ever contains tables with a genuine
    non-zero count — an empty dict is the honest "checked every table,
    found nothing" result, not an omission."""

    residual_rows: dict[str, int]
    residual_object_keys: list[str]

    @property
    def passed(self) -> bool:
        return not self.residual_rows and not self.residual_object_keys


class DeletionVerificationPort(Protocol):
    async def list_jobs_pending_verification(
        self, *, min_age_seconds: int, limit: int
    ) -> list[DeletionJob]:
        """Completed jobs with ``verified_at`` still NULL and
        ``completed_at`` at least ``min_age_seconds`` in the past — the
        real (if deliberately small in this MVP) decoupling gate: this
        sweep confirms durability past the deletion's own transaction,
        not just re-checks the instant it commits."""
        ...

    async def count_residual_rows(self, workspace_id: UUID) -> dict[str, int]:
        """Every tenant-scoped table, one real ``COUNT(*) WHERE
        workspace_id = $1`` query each — returns only tables with a
        non-zero count."""
        ...

    async def record_verification(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        report: ResidueReport,
        verified_at: datetime,
        audit_event_id: UUID,
    ) -> None:
        """Atomically: sets ``deletion_jobs.verified_at``/
        ``verification_passed`` and folds the report into ``evidence``,
        plus a system-plane (``workspace_id=NULL``) audit event
        recording the same result — same NULL-workspace shape as the
        deletion saga's own completion event, so it survives forever
        too."""
        ...
