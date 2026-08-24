from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aether.app.workspaces.purge_workspace import (
    WORKSPACE_DELETE_REQUESTED_EVENT_TYPE,
    DispatchWorkspaceDeletion,
)
from aether.domain.entities import DeletionJob, DeletionJobStatus
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.ingestion import FakeObjectStorage
from tests.unit.fakes.outbox import FakeOutboxRepository
from tests.unit.fakes.workspace_deletion import FakeWorkspaceDeletionRepository

pytestmark = pytest.mark.unit


def _queued_job(workspace_id: UUID, job_id: UUID) -> DeletionJob:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return DeletionJob(
        id=job_id,
        workspace_id=workspace_id,
        requested_by=uuid4(),
        status=DeletionJobStatus.QUEUED,
        evidence={},
        failure_reason=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


async def _enqueue(outbox: FakeOutboxRepository, *, workspace_id: UUID, job_id: UUID) -> UUID:
    entry_id = uuid4()
    await outbox.enqueue(
        id=entry_id,
        aggregate_type="workspace",
        aggregate_id=workspace_id,
        event_type=WORKSPACE_DELETE_REQUESTED_EVENT_TYPE,
        tenant_id=workspace_id,
        payload={"deletion_job_id": str(job_id)},
    )
    return entry_id


async def test_dispatch_purges_objects_and_completes_the_job() -> None:
    outbox = FakeOutboxRepository()
    repository = FakeWorkspaceDeletionRepository()
    object_storage = FakeObjectStorage(objects={"ws/doc-1": b"x", "ws/doc-2": b"y"})
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    repository.seed_object_keys(workspace_id, ["ws/doc-1", "ws/doc-2"])
    await _enqueue(outbox, workspace_id=workspace_id, job_id=job_id)
    use_case = DispatchWorkspaceDeletion(
        outbox=outbox,
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute()

    assert result.dispatched == 1
    assert result.failed == 0
    assert not await object_storage.object_exists(key="ws/doc-1")
    assert not await object_storage.object_exists(key="ws/doc-2")
    assert repository.hard_deleted_workspaces == [workspace_id]
    [completion] = repository.completions
    assert completion.job_id == job_id
    assert completion.evidence == {"objects_purged": 2}
    remaining = await outbox.fetch_pending(
        event_type=WORKSPACE_DELETE_REQUESTED_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert remaining == []


async def test_dispatch_handles_a_workspace_with_no_documents() -> None:
    outbox = FakeOutboxRepository()
    repository = FakeWorkspaceDeletionRepository()
    object_storage = FakeObjectStorage()
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    await _enqueue(outbox, workspace_id=workspace_id, job_id=job_id)
    use_case = DispatchWorkspaceDeletion(
        outbox=outbox,
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute()

    assert result.dispatched == 1
    [completion] = repository.completions
    assert completion.evidence == {"objects_purged": 0}


async def test_a_redelivered_already_complete_job_is_a_safe_no_op() -> None:
    """The literal issue #84 acceptance criterion: a mid-saga crash
    (repository.complete lands, but the worker dies before its own
    outbox.mark_dispatched call commits) followed by redelivery — the
    same outbox row fetched again, since dispatched_at never got set —
    must not double-purge or error. Drives DispatchWorkspaceDeletion's
    own idempotency unit (_purge_one) directly twice, since reproducing
    the actual crash window through the public execute()/outbox loop
    would require reaching into FakeOutboxRepository's internals to
    fake a torn commit that the real Postgres-backed adapter can't
    even represent (mark_dispatched either lands or the transaction
    that reached it never happened)."""
    repository = FakeWorkspaceDeletionRepository()
    object_storage = FakeObjectStorage(objects={"ws/doc-1": b"x"})
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    repository.seed_object_keys(workspace_id, ["ws/doc-1"])
    use_case = DispatchWorkspaceDeletion(
        outbox=FakeOutboxRepository(),
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )

    await use_case._purge_one(workspace_id=workspace_id, job_id=job_id)
    assert len(repository.completions) == 1
    assert not await object_storage.object_exists(key="ws/doc-1")

    await use_case._purge_one(workspace_id=workspace_id, job_id=job_id)

    # No second purge attempt and no second completion — the early
    # already-complete check short-circuited before any object_storage
    # or repository.complete call.
    assert len(repository.completions) == 1
    assert len(repository.hard_deleted_workspaces) == 1


async def test_dispatch_records_a_failure_without_losing_the_row() -> None:
    class FailingObjectStorage:
        async def delete(self, *, key: str) -> None:
            raise RuntimeError("simulated storage outage")

    outbox = FakeOutboxRepository()
    repository = FakeWorkspaceDeletionRepository()
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    repository.seed_object_keys(workspace_id, ["ws/doc-1"])
    await _enqueue(outbox, workspace_id=workspace_id, job_id=job_id)
    use_case = DispatchWorkspaceDeletion(
        outbox=outbox,
        repository=repository,
        object_storage=FailingObjectStorage(),
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute()

    assert result.dispatched == 0
    assert result.failed == 1
    assert repository.completions == []
    assert repository.hard_deleted_workspaces == []
    remaining = await outbox.fetch_pending(
        event_type=WORKSPACE_DELETE_REQUESTED_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert len(remaining) == 1
    assert remaining[0].attempts == 1
