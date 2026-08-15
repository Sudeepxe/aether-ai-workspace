"""DispatchEmailOutbox — the worker-side consumer of the transactional
outbox (§3.2.9, ADR-11.1's "queue-backed and retried"). One generic
event_type ("email.send") covers every email-sending feature (invitation,
password-reset, ...); the payload already has the exact shape EmailPort
needs, so this dispatcher has no per-feature branching to maintain.

Called by workers/main.py's poll loop in the running worker process, and
directly by tests — a worker dispatcher's correctness is proven by
calling the dispatch function and asserting on its effects, not by
running the loop and waiting on wall-clock timing.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from aether.ports.email import EmailMessage, EmailPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort

EMAIL_SEND_EVENT_TYPE = "email.send"
_MAX_ATTEMPTS = 5  # §3.6.2: "capped attempts (5, exp backoff) -> DLQ"

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: int
    failed: int


class DispatchEmailOutbox:
    def __init__(self, *, outbox: OutboxRepositoryPort, email: EmailPort, clock: ClockPort) -> None:
        self._outbox = outbox
        self._email = email
        self._clock = clock

    async def execute(self, *, batch_size: int = 20) -> DispatchResult:
        entries = await self._outbox.fetch_pending(
            event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=_MAX_ATTEMPTS, limit=batch_size
        )
        dispatched = 0
        failed = 0
        for entry in entries:
            message = EmailMessage(
                to=entry.payload["to"],
                subject=entry.payload["subject"],
                text_body=entry.payload["text_body"],
            )
            try:
                await self._email.send(message)
            except Exception:
                # At-least-once redelivery, capped attempts (§3.6.2) — a
                # send failure must never crash the batch or lose the
                # row; it just becomes eligible for a later retry (or, at
                # _MAX_ATTEMPTS, effectively a dead letter — see the
                # outbox migration's docstring on why there's no separate
                # DLQ table yet).
                await self._outbox.record_attempt_failure(entry.id)
                failed += 1
                log.error(
                    "email_dispatch_failed",
                    outbox_id=str(entry.id),
                    attempts=entry.attempts + 1,
                )
                continue
            await self._outbox.mark_dispatched(entry.id, dispatched_at=self._clock.now())
            dispatched += 1
        return DispatchResult(dispatched=dispatched, failed=failed)
