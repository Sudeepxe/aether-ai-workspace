"""DispatchWorkspaceDeletion — the worker-side consumer of DF-3's
``workspace.delete_requested`` outbox event (issue #84), mirroring
``app.notifications.dispatch_email_outbox``'s poll-and-dispatch shape.

Every step here is idempotent by construction, so at-least-once outbox
redelivery (no lease, no exactly-once — see ``ports.outbox``'s
docstring) just re-runs the whole saga safely on a crash mid-processing:
- ``list_object_keys`` re-reads from Postgres (still there until the
  final hard-delete), so a crash after purging some objects but before
  completing just re-lists (now-already-gone) keys and re-purges them —
  ``ObjectStoragePort.delete`` is itself idempotent (deleting an absent
  key is not an error).
- ``complete`` is one atomic transaction (job-complete + audit evidence
  + workspace hard-delete) — it either fully lands or fully rolls back,
  never a partial state.
- The very first check (``get_job`` — already COMPLETE means a prior
  attempt already finished, most likely a redelivery after this
  dispatcher's own ``mark_dispatched`` call failed to land) makes a
  second full pass on an already-finished job a safe no-op rather than
  a duplicate purge.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from aether.domain.entities import DeletionJobStatus
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort, IdPort
from aether.ports.storage import ObjectStoragePort
from aether.ports.workspace_deletion import WorkspaceDeletionPort

WORKSPACE_DELETE_REQUESTED_EVENT_TYPE = "workspace.delete_requested"
_MAX_ATTEMPTS = 5  # §3.6.2: "capped attempts (5, exp backoff) -> DLQ"

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: int
    failed: int


class DispatchWorkspaceDeletion:
    def __init__(
        self,
        *,
        outbox: OutboxRepositoryPort,
        repository: WorkspaceDeletionPort,
        object_storage: ObjectStoragePort,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        self._outbox = outbox
        self._repository = repository
        self._object_storage = object_storage
        self._clock = clock
        self._ids = ids

    async def execute(self, *, batch_size: int = 20) -> DispatchResult:
        entries = await self._outbox.fetch_pending(
            event_type=WORKSPACE_DELETE_REQUESTED_EVENT_TYPE,
            max_attempts=_MAX_ATTEMPTS,
            limit=batch_size,
        )
        dispatched = 0
        failed = 0
        for entry in entries:
            assert entry.tenant_id is not None  # noqa: S101 — invariant of this event type
            job_id = UUID(entry.payload["deletion_job_id"])
            try:
                await self._purge_one(workspace_id=entry.tenant_id, job_id=job_id)
            except Exception:
                await self._outbox.record_attempt_failure(entry.id)
                failed += 1
                log.error(
                    "workspace_deletion_failed",
                    outbox_id=str(entry.id),
                    deletion_job_id=str(job_id),
                    attempts=entry.attempts + 1,
                )
                continue
            await self._outbox.mark_dispatched(entry.id, dispatched_at=self._clock.now())
            dispatched += 1
        return DispatchResult(dispatched=dispatched, failed=failed)

    async def _purge_one(self, *, workspace_id: UUID, job_id: UUID) -> None:
        job = await self._repository.get_job(workspace_id, job_id)
        if job is not None and job.status == DeletionJobStatus.COMPLETE:
            return  # already finished — a redelivery after mark_dispatched failed to land
        await self._repository.mark_running(workspace_id, job_id)

        # list_object_keys already returns distinct keys (content-
        # addressed, ADR-3.8 — two documents with identical bytes in one
        # workspace share a key), so this count is "objects purged", not
        # "documents" (a document-row count would double-count a shared
        # key and overstate what was actually removed from storage).
        object_keys = await self._repository.list_object_keys(workspace_id)
        for key in object_keys:
            await self._object_storage.delete(key=key)

        evidence = {"objects_purged": len(object_keys)}
        await self._repository.complete(
            job_id=job_id,
            workspace_id=workspace_id,
            audit_event_id=self._ids.new_id(),
            evidence=evidence,
            completed_at=self._clock.now(),
        )
