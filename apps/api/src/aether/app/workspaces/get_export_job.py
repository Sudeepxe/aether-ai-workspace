"""GetExportJob use case (§4.3: GET /workspaces/{ws}/export-jobs/{id},
FR-AD-5). Mirrors GetDeletionJob's status-polling shape (issue #84),
plus one thing deletion has no equivalent of: on completion, mints a
short-lived presigned download URL for the assembled archive (ADR-3.8
— never a permanent public link, generated fresh on every poll rather
than stored, so it's always freshly-scoped from the moment of use)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import ExportJobStatus
from aether.domain.errors import ExportJobNotFoundError
from aether.ports.repositories import ExportJob, ExportJobRepositoryPort
from aether.ports.storage import ObjectStoragePort

_DOWNLOAD_URL_TTL_SECONDS = 3600  # 1 hour — generous for a real download, still short-lived


@dataclass(frozen=True, slots=True)
class GetExportJobCommand:
    workspace_id: UUID
    job_id: UUID


@dataclass(frozen=True, slots=True)
class ExportJobView:
    job: ExportJob
    download_url: str | None


class GetExportJob:
    def __init__(
        self, *, export_jobs: ExportJobRepositoryPort, object_storage: ObjectStoragePort
    ) -> None:
        self._export_jobs = export_jobs
        self._object_storage = object_storage

    async def execute(self, command: GetExportJobCommand) -> ExportJobView:
        job = await self._export_jobs.get_by_id(command.workspace_id, command.job_id)
        if job is None:
            raise ExportJobNotFoundError(str(command.job_id))
        download_url = None
        if job.status == ExportJobStatus.COMPLETE and job.archive_object_key is not None:
            download_url = self._object_storage.presign_download(
                key=job.archive_object_key, expires_seconds=_DOWNLOAD_URL_TTL_SECONDS
            )
        return ExportJobView(job=job, download_url=download_url)
