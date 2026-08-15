from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aether.app.notifications.dispatch_email_outbox import (
    EMAIL_SEND_EVENT_TYPE,
    DispatchEmailOutbox,
)
from aether.ports.email import EmailMessage
from tests.unit.fakes.auth import FakeClock
from tests.unit.fakes.outbox import FakeOutboxRepository

pytestmark = pytest.mark.unit


class FakeEmailPort:
    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self._fail_for = fail_for or set()

    async def send(self, message: EmailMessage) -> None:
        if message.to in self._fail_for:
            raise RuntimeError("simulated send failure")
        self.sent.append(message)


async def _enqueue(outbox: FakeOutboxRepository, *, to: str) -> UUID:
    entry_id = UUID(int=hash(to) & 0xFFFFFFFF)
    await outbox.enqueue(
        id=entry_id,
        aggregate_type="invitation",
        aggregate_id=UUID(int=1),
        event_type=EMAIL_SEND_EVENT_TYPE,
        tenant_id=UUID(int=2),
        payload={"to": to, "subject": "Hi", "text_body": "body"},
    )
    return entry_id


async def test_dispatch_sends_pending_emails_and_marks_them_dispatched() -> None:
    outbox = FakeOutboxRepository()
    email = FakeEmailPort()
    entry_id = await _enqueue(outbox, to="a@example.com")
    use_case = DispatchEmailOutbox(
        outbox=outbox, email=email, clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )

    result = await use_case.execute()

    assert result.dispatched == 1
    assert result.failed == 0
    assert email.sent[0].to == "a@example.com"
    remaining = await outbox.fetch_pending(
        event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert entry_id not in {e.id for e in remaining}


async def test_dispatch_records_a_failure_without_losing_the_row() -> None:
    outbox = FakeOutboxRepository()
    email = FakeEmailPort(fail_for={"broken@example.com"})
    entry_id = await _enqueue(outbox, to="broken@example.com")
    use_case = DispatchEmailOutbox(
        outbox=outbox, email=email, clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )

    result = await use_case.execute()

    assert result.dispatched == 0
    assert result.failed == 1
    # Still pending — a failed send must remain eligible for retry, not
    # vanish (§3.6.2 at-least-once redelivery).
    remaining = await outbox.fetch_pending(
        event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert entry_id in {e.id for e in remaining}
    assert remaining[0].attempts == 1


async def test_dispatch_stops_retrying_once_max_attempts_reached() -> None:
    outbox = FakeOutboxRepository()
    email = FakeEmailPort(fail_for={"broken@example.com"})
    entry_id = await _enqueue(outbox, to="broken@example.com")
    use_case = DispatchEmailOutbox(
        outbox=outbox, email=email, clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    )

    for _ in range(5):
        await use_case.execute()

    # Exhausted its attempts — no longer returned by fetch_pending (the
    # dead-letter signal; see the outbox migration's docstring).
    remaining = await outbox.fetch_pending(
        event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10
    )
    assert entry_id not in {e.id for e in remaining}
