from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aether.app.workspaces.verify_deletions import VerifyWorkspaceDeletions
from aether.domain.entities import DeletionJob, DeletionJobStatus
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.deletion_verification import FakeDeletionVerificationRepository
from tests.unit.fakes.ingestion import FakeObjectStorage

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _completed_job(workspace_id, job_id) -> DeletionJob:
    return DeletionJob(
        id=job_id,
        workspace_id=workspace_id,
        requested_by=uuid4(),
        status=DeletionJobStatus.COMPLETE,
        evidence={"objects_purged": 0},
        failure_reason=None,
        created_at=_NOW,
        updated_at=_NOW,
        completed_at=_NOW,
        verified_at=None,
        verification_passed=None,
    )


async def test_a_genuinely_clean_workspace_passes_verification() -> None:
    """The literal issue #86 acceptance criterion, at the logic level:
    the verifier isn't a rubber stamp — it must independently query real
    residue, and here that real query (via the fake) reports zero, so
    it correctly passes."""
    repository = FakeDeletionVerificationRepository()
    object_storage = FakeObjectStorage()
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_completed_job(workspace_id, job_id))
    # No seed_residue call — count_residual_rows defaults to {} (clean).
    use_case = VerifyWorkspaceDeletions(
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        min_age_seconds=0,
    )

    result = await use_case.execute()

    assert result.passed == 1
    assert result.failed == 0
    [recorded] = repository.recorded
    assert recorded.report.passed is True
    assert recorded.report.residual_rows == {}
    assert recorded.report.residual_object_keys == []


async def test_a_workspace_with_real_leftover_rows_fails_verification_loudly() -> None:
    """The other half of "not a rubber stamp": a genuinely non-zero
    residual-row count must make the verifier report failure, not
    silently pass. Simulates the deletion saga's cascade having missed
    a table."""
    repository = FakeDeletionVerificationRepository()
    object_storage = FakeObjectStorage()
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_completed_job(workspace_id, job_id))
    repository.seed_residue(workspace_id, {"messages": 3, "threads": 1})
    use_case = VerifyWorkspaceDeletions(
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        min_age_seconds=0,
    )

    result = await use_case.execute()

    assert result.passed == 0
    assert result.failed == 1
    [recorded] = repository.recorded
    assert recorded.report.passed is False
    assert recorded.report.residual_rows == {"messages": 3, "threads": 1}


async def test_a_stray_object_in_storage_alone_fails_verification() -> None:
    """Zero residual DB rows but a real leftover object — the object-
    storage dimension must independently fail the check too, not just
    the DB one."""
    repository = FakeDeletionVerificationRepository()
    workspace_id, job_id = uuid4(), uuid4()
    object_storage = FakeObjectStorage(objects={f"{workspace_id}/stray.md": b"leftover"})
    repository.seed_job(_completed_job(workspace_id, job_id))
    use_case = VerifyWorkspaceDeletions(
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        min_age_seconds=0,
    )

    result = await use_case.execute()

    assert result.passed == 0
    assert result.failed == 1
    [recorded] = repository.recorded
    assert recorded.report.residual_object_keys == [f"{workspace_id}/stray.md"]


async def test_an_already_verified_job_is_never_picked_up_again() -> None:
    repository = FakeDeletionVerificationRepository()
    workspace_id, job_id = uuid4(), uuid4()
    already_verified = replace(_completed_job(workspace_id, job_id), verified_at=_NOW)
    repository.seed_job(already_verified)
    use_case = VerifyWorkspaceDeletions(
        repository=repository,
        object_storage=FakeObjectStorage(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        min_age_seconds=0,
    )

    result = await use_case.execute()

    assert result.passed == 0
    assert result.failed == 0
    assert repository.recorded == []
