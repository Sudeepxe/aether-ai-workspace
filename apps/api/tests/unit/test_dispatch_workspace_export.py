from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aether.app.workspaces.build_export import (
    EXPORT_SCHEMA_VERSION,
    WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE,
    DispatchWorkspaceExport,
)
from aether.domain.entities import ExportJobStatus
from aether.ports.workspace_export import (
    ExportedCitation,
    ExportedDocument,
    ExportedFeedback,
    ExportedMembership,
    ExportedMessage,
    ExportedThread,
    ExportJob,
    WorkspaceExportData,
)
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.ingestion import FakeObjectStorage
from tests.unit.fakes.outbox import FakeOutboxRepository
from tests.unit.fakes.workspace_export import FakeWorkspaceExportRepository

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _queued_job(workspace_id: UUID, job_id: UUID) -> ExportJob:
    return ExportJob(
        id=job_id,
        workspace_id=workspace_id,
        requested_by=uuid4(),
        status=ExportJobStatus.QUEUED,
        archive_object_key=None,
        evidence={},
        failure_reason=None,
        created_at=_NOW,
        updated_at=_NOW,
        completed_at=None,
    )


def _rich_export_data(workspace_id: UUID, *, object_key: str) -> WorkspaceExportData:
    thread_id, message_id, document_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    return WorkspaceExportData(
        workspace_id=workspace_id,
        workspace_name="Acme",
        workspace_slug="acme",
        workspace_created_at=_NOW,
        memberships=[ExportedMembership(user_id=user_id, role="owner", created_at=_NOW)],
        threads=[
            ExportedThread(
                id=thread_id,
                title="Pricing questions",
                created_at=_NOW,
                messages=[
                    ExportedMessage(
                        id=message_id,
                        seq=1,
                        role="assistant",
                        content="Acme costs $10/mo.",
                        grounded=True,
                        created_at=_NOW,
                        citations=[
                            ExportedCitation(
                                document_title="pricing.md",
                                section_path="Pricing",
                                page_start=1,
                                page_end=1,
                            )
                        ],
                    )
                ],
            )
        ],
        documents=[
            ExportedDocument(
                id=document_id,
                filename="pricing.md",
                mime="text/markdown",
                size_bytes=18,
                status="ready",
                object_key=object_key,
                created_at=_NOW,
            )
        ],
        feedback=[
            ExportedFeedback(
                message_id=message_id, user_id=user_id, rating="up", reason=None, created_at=_NOW
            )
        ],
        usage_total_cost_microcents=1234,
        usage_request_count=3,
    )


async def _enqueue(outbox: FakeOutboxRepository, *, workspace_id: UUID, job_id: UUID) -> UUID:
    entry_id = uuid4()
    await outbox.enqueue(
        id=entry_id,
        aggregate_type="workspace",
        aggregate_id=workspace_id,
        event_type=WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE,
        tenant_id=workspace_id,
        payload={"export_job_id": str(job_id)},
    )
    return entry_id


async def test_dispatch_assembles_a_real_archive_with_json_and_the_original_file() -> None:
    outbox = FakeOutboxRepository()
    repository = FakeWorkspaceExportRepository()
    object_storage = FakeObjectStorage(objects={"acme/pricing.md": b"Acme costs $10/mo."})
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    data = _rich_export_data(workspace_id, object_key="acme/pricing.md")
    repository.seed_export_data(workspace_id, data)
    await _enqueue(outbox, workspace_id=workspace_id, job_id=job_id)
    use_case = DispatchWorkspaceExport(
        outbox=outbox,
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute()

    assert result.dispatched == 1
    assert result.failed == 0
    [completion] = repository.completions
    assert completion.workspace_id == workspace_id
    assert completion.archive_object_key == f"exports/{workspace_id}/{job_id}.zip"
    assert completion.evidence == {
        "threads": 1,
        "messages": 1,
        "documents": 1,
        "files_bundled": 1,
        "archive_size_bytes": completion.evidence["archive_size_bytes"],
    }

    # The archive was really uploaded — read it back and verify its
    # real contents, not just that *something* was written.
    archive_bytes = await object_storage.download(key=completion.archive_object_key)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = archive.namelist()
        assert "export.json" in names
        assert f"files/{data.documents[0].id}_pricing.md" in names

        export_json = json.loads(archive.read("export.json"))
        assert export_json["export_version"] == EXPORT_SCHEMA_VERSION
        assert export_json["workspace"]["name"] == "Acme"
        assert len(export_json["threads"]) == 1
        assert export_json["threads"][0]["messages"][0]["content"] == "Acme costs $10/mo."
        assert export_json["threads"][0]["messages"][0]["citations"][0]["document_title"] == (
            "pricing.md"
        )
        assert export_json["documents"][0]["filename"] == "pricing.md"
        assert export_json["feedback"][0]["rating"] == "up"
        assert export_json["usage"] == {"total_cost_microcents": 1234, "request_count": 3}

        # The bundled file's bytes match the real original byte-for-byte.
        bundled_file = archive.read(f"files/{data.documents[0].id}_pricing.md")
        assert bundled_file == b"Acme costs $10/mo."


async def test_dispatch_handles_a_workspace_with_no_documents() -> None:
    outbox = FakeOutboxRepository()
    repository = FakeWorkspaceExportRepository()
    object_storage = FakeObjectStorage()
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    repository.seed_export_data(
        workspace_id,
        WorkspaceExportData(
            workspace_id=workspace_id,
            workspace_name="Empty",
            workspace_slug="empty",
            workspace_created_at=_NOW,
            memberships=[],
            threads=[],
            documents=[],
            feedback=[],
            usage_total_cost_microcents=0,
            usage_request_count=0,
        ),
    )
    await _enqueue(outbox, workspace_id=workspace_id, job_id=job_id)
    use_case = DispatchWorkspaceExport(
        outbox=outbox,
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute()

    assert result.dispatched == 1
    [completion] = repository.completions
    assert completion.evidence["documents"] == 0
    assert completion.evidence["files_bundled"] == 0


async def test_a_redelivered_already_complete_job_is_a_safe_no_op() -> None:
    repository = FakeWorkspaceExportRepository()
    object_storage = FakeObjectStorage(objects={"acme/pricing.md": b"Acme costs $10/mo."})
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    repository.seed_export_data(
        workspace_id, _rich_export_data(workspace_id, object_key="acme/pricing.md")
    )
    use_case = DispatchWorkspaceExport(
        outbox=FakeOutboxRepository(),
        repository=repository,
        object_storage=object_storage,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
    )

    await use_case._build_one(workspace_id=workspace_id, job_id=job_id)
    assert len(repository.completions) == 1

    await use_case._build_one(workspace_id=workspace_id, job_id=job_id)

    assert len(repository.completions) == 1


async def test_dispatch_records_a_failure_without_losing_the_row() -> None:
    class FailingObjectStorage:
        async def download(self, *, key: str) -> bytes:
            raise RuntimeError("simulated storage outage")

    outbox = FakeOutboxRepository()
    repository = FakeWorkspaceExportRepository()
    workspace_id, job_id = uuid4(), uuid4()
    repository.seed_job(_queued_job(workspace_id, job_id))
    repository.seed_export_data(
        workspace_id, _rich_export_data(workspace_id, object_key="acme/pricing.md")
    )
    await _enqueue(outbox, workspace_id=workspace_id, job_id=job_id)
    use_case = DispatchWorkspaceExport(
        outbox=outbox,
        repository=repository,
        object_storage=FailingObjectStorage(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute()

    assert result.dispatched == 0
    assert result.failed == 1
    assert repository.completions == []
    remaining = await outbox.fetch_pending(
        event_type=WORKSPACE_EXPORT_REQUESTED_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert len(remaining) == 1
    assert remaining[0].attempts == 1
