"""Worker-plane port for the workspace-deletion saga (DF-3, issue #84).

Pool-bound, not connection-bound — same sibling role to the API-plane
``DeletionJobRepositoryPort`` as ``ports.chat.MessageStorePort`` has to
``MessageRepositoryPort``: this one is meant to be called *outside* an
already-open request transaction, by the worker's own poll loop.

``complete`` is the one method that matters most for the saga's safety
properties: it must atomically (a) mark the job complete with its real
evidence, (b) write a system-plane ``workspace.deleted`` audit event
(``workspace_id=NULL`` — the same NULL-tenant-context row shape already
used for auth-plane events, chosen specifically so the evidence survives
the very cascade it documents; see the deletion_jobs migration's
docstring), and (c) hard-delete the ``workspaces`` row, which cascades
every other child table in one transaction. Splitting these into
separate calls would let a crash between them leave a job marked
complete whose workspace still exists, or vice versa — an integrity gap
this port's shape rules out by construction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from aether.domain.entities import DeletionJob, DeletionJobStatus

__all__ = ["DeletionJob", "DeletionJobStatus", "WorkspaceDeletionPort"]


class WorkspaceDeletionPort(Protocol):
    """Every method takes ``workspace_id`` explicitly, even where a bare
    ``job_id`` would identify the row — ``deletion_jobs`` is RLS-scoped
    on ``workspace_id``, and the outbox entry that drives this saga
    always carries it (``tenant_id`` on the entry itself), so there's no
    chicken-and-egg lookup needed to know which tenant context to set
    before the very first query."""

    async def get_job(self, workspace_id: UUID, job_id: UUID) -> DeletionJob | None: ...

    async def mark_running(self, workspace_id: UUID, job_id: UUID) -> None:
        """A no-op if the job is already past QUEUED (redelivery)."""
        ...

    async def list_object_keys(self, workspace_id: UUID) -> list[str]:
        """Every document's object_key for this workspace, regardless of
        the document's own ``deleted_at`` — an individually soft-deleted
        document's bytes are never purged today (issue #48's DeleteDocument
        is sync-content/async-storage split, and nothing consumes its
        ``document.deleted`` event yet), so a workspace deletion is the
        first point any of them are provably removed. Deduplicated by
        the caller before use: object keys are content-addressed (ADR-3.8),
        so two documents with identical bytes in one workspace share a key."""
        ...

    async def complete(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        audit_event_id: UUID,
        evidence: dict[str, Any],
        completed_at: datetime,
    ) -> None: ...
