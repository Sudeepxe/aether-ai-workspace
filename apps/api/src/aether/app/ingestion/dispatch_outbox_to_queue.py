"""DispatchIngestionOutbox — relays durable ``document.uploaded`` outbox
rows into the per-tenant fair-queued IngestionQueuePort (§3.2.7,
§3.2.9's D3-3). Same "app writes state + outbox row in one ACID
transaction, a dispatcher relays outbox -> transport" shape as
DispatchEmailOutbox, but targeting the Streams-based fair queue instead
of acting directly — ingestion specifically needs NFR-S-2's cross-tenant
fairness, which processing the outbox in ``created_at`` order (as the
email dispatcher does) cannot provide: a bulk upload's rows would sit
first in line, starving every other workspace's single upload.

Dispatch order off the outbox is still plain FIFO — that's fine here,
since dispatching is O(1) per row (just an XADD) and doesn't hold up
processing; the fairness lives entirely in the queue's own round-robin
``claim_next()``, not in this dispatcher.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from aether.observability.tracing import linked_span
from aether.ports.ingestion_queue import IngestionQueuePort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort

DOCUMENT_UPLOADED_EVENT_TYPE = "document.uploaded"
_MAX_ATTEMPTS = 5

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: int
    failed: int


class DispatchIngestionOutbox:
    def __init__(
        self, *, outbox: OutboxRepositoryPort, queue: IngestionQueuePort, clock: ClockPort
    ) -> None:
        self._outbox = outbox
        self._queue = queue
        self._clock = clock

    async def execute(self, *, batch_size: int = 20) -> DispatchResult:
        entries = await self._outbox.fetch_pending(
            event_type=DOCUMENT_UPLOADED_EVENT_TYPE, max_attempts=_MAX_ATTEMPTS, limit=batch_size
        )
        dispatched = 0
        failed = 0
        for entry in entries:
            # Invariant of this event type, not a general OutboxEntry
            # guarantee: a document.uploaded row is always produced with
            # its workspace as tenant_id (see the document-creation use
            # case that writes it).
            assert entry.tenant_id is not None  # noqa: S101
            with linked_span("outbox.dispatch.document.uploaded", entry.trace_context):
                try:
                    await self._queue.enqueue(
                        tenant_id=entry.tenant_id,
                        payload={
                            **_stringify(entry.payload),
                            "outbox_id": str(entry.id),
                            "aggregate_id": str(entry.aggregate_id),
                            # Forwarded, not consumed here — the relay
                            # itself only XADDs; the real processing (and
                            # the trace this context resumes) happens
                            # later in run_ingestion_consumer.
                            "trace_context": json.dumps(entry.trace_context),
                        },
                    )
                except Exception:
                    await self._outbox.record_attempt_failure(entry.id)
                    failed += 1
                    log.error(
                        "ingestion_dispatch_failed",
                        outbox_id=str(entry.id),
                        attempts=entry.attempts + 1,
                    )
                    continue
                await self._outbox.mark_dispatched(entry.id, dispatched_at=self._clock.now())
                dispatched += 1
        return DispatchResult(dispatched=dispatched, failed=failed)


def _stringify(payload: dict[str, Any]) -> dict[str, str]:
    """Redis Streams fields are flat str->str pairs; the outbox payload
    is jsonb (dict[str, Any]) — non-string values are serialized so
    nothing is silently dropped."""
    return {k: v if isinstance(v, str) else json.dumps(v) for k, v in payload.items()}
