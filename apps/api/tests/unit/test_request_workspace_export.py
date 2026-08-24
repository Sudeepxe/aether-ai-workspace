from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.workspaces.request_export import (
    RequestWorkspaceExport,
    RequestWorkspaceExportCommand,
)
from aether.domain.entities import ExportJobStatus
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.outbox import FakeOutboxRepository
from tests.unit.fakes.workspaces import FakeAuditLog, FakeExportJobRepository

pytestmark = pytest.mark.unit


async def test_request_export_creates_a_queued_job_and_enqueues_the_saga() -> None:
    export_jobs = FakeExportJobRepository()
    outbox = FakeOutboxRepository()
    audit_log = FakeAuditLog()
    workspace_id, actor_id = uuid4(), uuid4()
    use_case = RequestWorkspaceExport(
        export_jobs=export_jobs, outbox=outbox, audit_log=audit_log, ids=FakeIdGenerator()
    )

    job = await use_case.execute(
        RequestWorkspaceExportCommand(workspace_id=workspace_id, actor_user_id=actor_id)
    )

    assert job.workspace_id == workspace_id
    assert job.status == ExportJobStatus.QUEUED
    persisted = await export_jobs.get_by_id(workspace_id, job.id)
    assert persisted is not None
    assert persisted.id == job.id
    enqueued = await outbox.fetch_pending(
        event_type="workspace.export_requested", max_attempts=5, limit=10
    )
    assert len(enqueued) == 1
    assert enqueued[0].tenant_id == workspace_id
    assert enqueued[0].payload == {"export_job_id": str(job.id)}
    assert audit_log.recorded[0].action == "workspace.export_requested"
