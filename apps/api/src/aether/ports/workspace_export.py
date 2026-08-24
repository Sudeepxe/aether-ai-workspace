"""Worker-plane port for the tenant-data-export saga (FR-AD-5, issue #85).

Pool-bound, mirroring ``ports.workspace_deletion.WorkspaceDeletionPort``'s
shape exactly. ``fetch_export_data`` returns purpose-built, JSON-shaped
DTOs rather than the full domain entities (``Message``, ``Document``,
...) — an export snapshot only ever needs a subset of each row's fields
(no internal bookkeeping like ``failure_stage`` or ``superseded_by``),
and adapters may not import other adapters to reuse their row-to-entity
converters (import-linter's "adapters depend only on ports" contract),
so a lighter, export-specific shape avoids re-deriving full entities
from partial data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from aether.domain.entities import ExportJob, ExportJobStatus

__all__ = [
    "ExportJob",
    "ExportJobStatus",
    "ExportedCitation",
    "ExportedDocument",
    "ExportedFeedback",
    "ExportedMembership",
    "ExportedMessage",
    "ExportedThread",
    "WorkspaceExportData",
    "WorkspaceExportPort",
]


@dataclass(frozen=True, slots=True)
class ExportedCitation:
    document_title: str
    section_path: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class ExportedMessage:
    id: UUID
    seq: int
    role: str
    content: str
    grounded: bool
    created_at: datetime
    citations: list[ExportedCitation]


@dataclass(frozen=True, slots=True)
class ExportedThread:
    id: UUID
    title: str | None
    created_at: datetime
    messages: list[ExportedMessage]


@dataclass(frozen=True, slots=True)
class ExportedDocument:
    id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    object_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExportedMembership:
    user_id: UUID
    role: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExportedFeedback:
    message_id: UUID
    user_id: UUID
    rating: str
    reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceExportData:
    workspace_id: UUID
    workspace_name: str
    workspace_slug: str
    workspace_created_at: datetime
    memberships: list[ExportedMembership]
    threads: list[ExportedThread]
    documents: list[ExportedDocument]
    feedback: list[ExportedFeedback]
    usage_total_cost_microcents: int
    usage_request_count: int


class WorkspaceExportPort(Protocol):
    """Every method takes ``workspace_id`` explicitly — same reasoning
    as ``WorkspaceDeletionPort``: ``export_jobs`` is RLS-scoped, and the
    outbox entry driving this saga always carries the workspace id."""

    async def get_job(self, workspace_id: UUID, job_id: UUID) -> ExportJob | None: ...

    async def mark_running(self, workspace_id: UUID, job_id: UUID) -> None:
        """A no-op if the job is already past QUEUED (redelivery)."""
        ...

    async def fetch_export_data(self, workspace_id: UUID) -> WorkspaceExportData: ...

    async def complete(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        archive_object_key: str,
        evidence: dict[str, Any],
        completed_at: datetime,
    ) -> None: ...
