from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.workspaces.get_deletion_job import GetDeletionJob, GetDeletionJobCommand
from aether.domain.errors import DeletionJobNotFoundError
from tests.unit.fakes.workspaces import FakeDeletionJobRepository

pytestmark = pytest.mark.unit


async def test_get_deletion_job_returns_the_persisted_job() -> None:
    deletion_jobs = FakeDeletionJobRepository()
    workspace_id, user_id = uuid4(), uuid4()
    created = await deletion_jobs.create(
        id=uuid4(), workspace_id=workspace_id, requested_by=user_id
    )

    result = await GetDeletionJob(deletion_jobs=deletion_jobs).execute(
        GetDeletionJobCommand(workspace_id=workspace_id, job_id=created.id)
    )

    assert result.id == created.id
    assert result.workspace_id == workspace_id


async def test_get_deletion_job_raises_not_found_for_an_unknown_id() -> None:
    deletion_jobs = FakeDeletionJobRepository()
    workspace_id = uuid4()

    with pytest.raises(DeletionJobNotFoundError):
        await GetDeletionJob(deletion_jobs=deletion_jobs).execute(
            GetDeletionJobCommand(workspace_id=workspace_id, job_id=uuid4())
        )


async def test_get_deletion_job_raises_not_found_across_workspaces() -> None:
    deletion_jobs = FakeDeletionJobRepository()
    workspace_a, workspace_b, user_id = uuid4(), uuid4(), uuid4()
    created = await deletion_jobs.create(id=uuid4(), workspace_id=workspace_a, requested_by=user_id)

    with pytest.raises(DeletionJobNotFoundError):
        await GetDeletionJob(deletion_jobs=deletion_jobs).execute(
            GetDeletionJobCommand(workspace_id=workspace_b, job_id=created.id)
        )
