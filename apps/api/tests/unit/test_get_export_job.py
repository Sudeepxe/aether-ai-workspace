from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from aether.app.workspaces.get_export_job import GetExportJob, GetExportJobCommand
from aether.domain.entities import ExportJobStatus
from aether.domain.errors import ExportJobNotFoundError
from tests.unit.fakes.workspaces import FakeExportJobRepository

pytestmark = pytest.mark.unit


class _FakeObjectStorage:
    def presign_download(self, *, key: str, expires_seconds: int) -> str:
        return f"https://storage.example/{key}?expires={expires_seconds}"


async def test_get_export_job_returns_no_download_url_while_queued() -> None:
    export_jobs = FakeExportJobRepository()
    workspace_id, user_id = uuid4(), uuid4()
    created = await export_jobs.create(id=uuid4(), workspace_id=workspace_id, requested_by=user_id)
    use_case = GetExportJob(export_jobs=export_jobs, object_storage=_FakeObjectStorage())

    view = await use_case.execute(GetExportJobCommand(workspace_id=workspace_id, job_id=created.id))

    assert view.job.id == created.id
    assert view.download_url is None


async def test_get_export_job_mints_a_download_url_once_complete() -> None:
    export_jobs = FakeExportJobRepository()
    workspace_id, user_id = uuid4(), uuid4()
    created = await export_jobs.create(id=uuid4(), workspace_id=workspace_id, requested_by=user_id)
    export_jobs.seed(
        replace(
            created,
            status=ExportJobStatus.COMPLETE,
            archive_object_key=f"exports/{workspace_id}/{created.id}.zip",
        )
    )
    use_case = GetExportJob(export_jobs=export_jobs, object_storage=_FakeObjectStorage())

    view = await use_case.execute(GetExportJobCommand(workspace_id=workspace_id, job_id=created.id))

    assert view.download_url is not None
    assert f"exports/{workspace_id}/{created.id}.zip" in view.download_url


async def test_get_export_job_raises_not_found_for_an_unknown_id() -> None:
    export_jobs = FakeExportJobRepository()
    workspace_id = uuid4()

    with pytest.raises(ExportJobNotFoundError):
        await GetExportJob(export_jobs=export_jobs, object_storage=_FakeObjectStorage()).execute(
            GetExportJobCommand(workspace_id=workspace_id, job_id=uuid4())
        )
