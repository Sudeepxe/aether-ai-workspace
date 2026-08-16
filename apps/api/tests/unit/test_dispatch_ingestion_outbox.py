from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aether.app.ingestion.dispatch_outbox_to_queue import (
    DOCUMENT_UPLOADED_EVENT_TYPE,
    DispatchIngestionOutbox,
)
from tests.unit.fakes.auth import FakeClock
from tests.unit.fakes.outbox import FakeOutboxRepository

pytestmark = pytest.mark.unit


class FakeIngestionQueue:
    def __init__(self, *, fail_for: set[UUID] | None = None) -> None:
        self.enqueued: list[tuple[UUID, dict[str, str]]] = []
        self._fail_for = fail_for or set()

    async def enqueue(self, *, tenant_id: UUID, payload: dict[str, str]) -> None:
        if tenant_id in self._fail_for:
            raise RuntimeError("simulated enqueue failure")
        self.enqueued.append((tenant_id, payload))


async def _enqueue_outbox_row(
    outbox: FakeOutboxRepository, *, entry_id: UUID, tenant_id: UUID
) -> None:
    await outbox.enqueue(
        id=entry_id,
        aggregate_type="document",
        aggregate_id=UUID(int=99),
        event_type=DOCUMENT_UPLOADED_EVENT_TYPE,
        tenant_id=tenant_id,
        payload={"object_key": "workspace/doc.pdf", "doc_id": str(UUID(int=99))},
    )


async def test_dispatch_relays_pending_events_into_the_queue_and_marks_dispatched() -> None:
    outbox = FakeOutboxRepository()
    queue = FakeIngestionQueue()
    tenant_id = UUID(int=1)
    entry_id = UUID(int=2)
    await _enqueue_outbox_row(outbox, entry_id=entry_id, tenant_id=tenant_id)
    use_case = DispatchIngestionOutbox(
        outbox=outbox, queue=queue, clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )

    result = await use_case.execute()

    assert result.dispatched == 1
    assert result.failed == 0
    assert len(queue.enqueued) == 1
    dispatched_tenant, payload = queue.enqueued[0]
    assert dispatched_tenant == tenant_id
    assert payload["object_key"] == "workspace/doc.pdf"
    assert payload["outbox_id"] == str(entry_id)

    remaining = await outbox.fetch_pending(
        event_type=DOCUMENT_UPLOADED_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert entry_id not in {e.id for e in remaining}


async def test_dispatch_records_a_failure_without_losing_the_row() -> None:
    outbox = FakeOutboxRepository()
    tenant_id = UUID(int=1)
    entry_id = UUID(int=2)
    queue = FakeIngestionQueue(fail_for={tenant_id})
    await _enqueue_outbox_row(outbox, entry_id=entry_id, tenant_id=tenant_id)
    use_case = DispatchIngestionOutbox(
        outbox=outbox, queue=queue, clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )

    result = await use_case.execute()

    assert result.dispatched == 0
    assert result.failed == 1
    remaining = await outbox.fetch_pending(
        event_type=DOCUMENT_UPLOADED_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert entry_id in {e.id for e in remaining}
    assert remaining[0].attempts == 1


async def test_non_string_payload_values_are_serialized_for_stream_fields() -> None:
    outbox = FakeOutboxRepository()
    queue = FakeIngestionQueue()
    tenant_id = UUID(int=1)
    entry_id = UUID(int=2)
    await outbox.enqueue(
        id=entry_id,
        aggregate_type="document",
        aggregate_id=UUID(int=99),
        event_type=DOCUMENT_UPLOADED_EVENT_TYPE,
        tenant_id=tenant_id,
        payload={"size_bytes": 1234, "tags": ["a", "b"]},
    )
    use_case = DispatchIngestionOutbox(
        outbox=outbox, queue=queue, clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )

    await use_case.execute()

    _, payload = queue.enqueued[0]
    assert payload["size_bytes"] == "1234"
    assert payload["tags"] == '["a", "b"]'
