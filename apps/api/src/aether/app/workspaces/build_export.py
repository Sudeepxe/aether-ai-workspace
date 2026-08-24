"""DispatchWorkspaceExport — the worker-side consumer of FR-AD-5's
``workspace.export_requested`` outbox event (issue #85), mirroring
``app.workspaces.purge_workspace.DispatchWorkspaceDeletion``'s poll-and-
dispatch shape (issue #84) and its same idempotent-by-construction
safety argument: ``fetch_export_data`` re-reads from Postgres (nothing
here mutates tenant data, so a crash mid-assembly changes nothing to
redo), ``object_storage.upload`` overwrites the same job-scoped key on
a retry rather than accumulating duplicates, and the leading
already-COMPLETE check makes a redelivered-after-success run a safe
no-op before any work happens.

``EXPORT_SCHEMA_VERSION`` is carried in the JSON's own top-level
``export_version`` field (not just this module) so a future shape
change never silently breaks an already-downloaded export's consumers
— they can check the field themselves.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from aether.domain.entities import ExportJobStatus
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort, IdPort
from aether.ports.storage import ObjectStoragePort
from aether.ports.workspace_export import WorkspaceExportData, WorkspaceExportPort

WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE = "workspace.export_requested"
_MAX_ATTEMPTS = 5  # §3.6.2: "capped attempts (5, exp backoff) -> DLQ"
EXPORT_SCHEMA_VERSION = 1

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: int
    failed: int


class DispatchWorkspaceExport:
    def __init__(
        self,
        *,
        outbox: OutboxRepositoryPort,
        repository: WorkspaceExportPort,
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
            event_type=WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE,
            max_attempts=_MAX_ATTEMPTS,
            limit=batch_size,
        )
        dispatched = 0
        failed = 0
        for entry in entries:
            assert entry.tenant_id is not None  # noqa: S101 — invariant of this event type
            job_id = UUID(entry.payload["export_job_id"])
            try:
                await self._build_one(workspace_id=entry.tenant_id, job_id=job_id)
            except Exception:
                await self._outbox.record_attempt_failure(entry.id)
                failed += 1
                log.error(
                    "workspace_export_failed",
                    outbox_id=str(entry.id),
                    export_job_id=str(job_id),
                    attempts=entry.attempts + 1,
                )
                continue
            await self._outbox.mark_dispatched(entry.id, dispatched_at=self._clock.now())
            dispatched += 1
        return DispatchResult(dispatched=dispatched, failed=failed)

    async def _build_one(self, *, workspace_id: UUID, job_id: UUID) -> None:
        job = await self._repository.get_job(workspace_id, job_id)
        if job is not None and job.status == ExportJobStatus.COMPLETE:
            return  # already finished — a redelivery after mark_dispatched failed to land

        await self._repository.mark_running(workspace_id, job_id)

        data = await self._repository.fetch_export_data(workspace_id)
        files: dict[str, bytes] = {}
        for document in data.documents:
            files[f"files/{document.id}_{document.filename}"] = await self._object_storage.download(
                key=document.object_key
            )

        export_json = _build_export_json(data)
        archive = await asyncio.to_thread(_build_archive, export_json, files)
        archive_object_key = f"exports/{workspace_id}/{job_id}.zip"
        await self._object_storage.upload(
            key=archive_object_key, content=archive, content_type="application/zip"
        )

        evidence = {
            "threads": len(data.threads),
            "messages": sum(len(t.messages) for t in data.threads),
            "documents": len(data.documents),
            "files_bundled": len(files),
            "archive_size_bytes": len(archive),
        }
        await self._repository.complete(
            job_id=job_id,
            workspace_id=workspace_id,
            archive_object_key=archive_object_key,
            evidence=evidence,
            completed_at=self._clock.now(),
        )


def _build_export_json(data: WorkspaceExportData) -> dict[str, Any]:
    return {
        "export_version": EXPORT_SCHEMA_VERSION,
        "workspace": {
            "id": str(data.workspace_id),
            "name": data.workspace_name,
            "slug": data.workspace_slug,
            "created_at": data.workspace_created_at.isoformat(),
        },
        "memberships": [
            {"user_id": str(m.user_id), "role": m.role, "created_at": m.created_at.isoformat()}
            for m in data.memberships
        ],
        "threads": [
            {
                "id": str(t.id),
                "title": t.title,
                "created_at": t.created_at.isoformat(),
                "messages": [
                    {
                        "id": str(m.id),
                        "seq": m.seq,
                        "role": m.role,
                        "content": m.content,
                        "grounded": m.grounded,
                        "created_at": m.created_at.isoformat(),
                        "citations": [
                            {
                                "document_title": c.document_title,
                                "section_path": c.section_path,
                                "page_start": c.page_start,
                                "page_end": c.page_end,
                            }
                            for c in m.citations
                        ],
                    }
                    for m in t.messages
                ],
            }
            for t in data.threads
        ],
        "documents": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "mime": d.mime,
                "size_bytes": d.size_bytes,
                "status": d.status,
                "created_at": d.created_at.isoformat(),
                "archive_path": f"files/{d.id}_{d.filename}",
            }
            for d in data.documents
        ],
        "feedback": [
            {
                "message_id": str(f.message_id),
                "user_id": str(f.user_id),
                "rating": f.rating,
                "reason": f.reason,
                "created_at": f.created_at.isoformat(),
            }
            for f in data.feedback
        ],
        "usage": {
            "total_cost_microcents": data.usage_total_cost_microcents,
            "request_count": data.usage_request_count,
        },
    }


def _build_archive(export_json: dict[str, Any], files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export.json", json.dumps(export_json, indent=2))
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()
