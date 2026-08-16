"""IngestionQueuePort (§3.2.7, §3.2.9's D3-3): per-tenant fair-queued
delivery for the ingestion pipeline. The transactional outbox remains
the durable source of truth for "this event must not be lost" — a
dispatcher relays pending outbox rows into this queue (see
app/ingestion/dispatch_outbox_to_queue.py); this port is purely about
*fair, at-least-once delivery* once an event is already durably recorded.

Distinct from the plain outbox-poll pattern already used for email
(app/notifications/dispatch_email_outbox.py): that pattern processes
strictly in ``created_at`` order, which is exactly the head-of-line
blocking NFR-S-2 forbids for ingestion — a bulk upload from one
workspace must not starve every other workspace's single upload. Fair
round-robin scheduling across tenants is this port's whole reason to
exist as something other than another outbox consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    stream_message_id: str
    tenant_id: UUID
    payload: dict[str, str]
    delivery_count: int
    """How many times this exact message has been delivered to a
    consumer (Redis Streams' own PEL-tracked count) — 1 on first
    delivery, incremented on every reclaim after a failure. Compared
    against a configured max before deciding retry vs. dead-letter."""


class IngestionQueuePort(Protocol):
    async def enqueue(self, *, tenant_id: UUID, payload: dict[str, str]) -> None: ...

    async def claim_next(self) -> QueuedMessage | None:
        """Round-robin across tenants with pending work: at most one
        message per call, from whichever tenant is next in rotation —
        the mechanism behind "no head-of-line blocking across tenants"
        (NFR-S-2). Prefers reclaiming an already-delivered-but-failed
        message for that tenant (a retry) over reading a fresh one, so
        retries don't silently starve behind new work. Returns None
        only when no tenant has anything pending right now."""
        ...

    async def ack(self, message: QueuedMessage) -> None:
        """Marks successful processing — permanently removes the
        message from its consumer group's pending-entries list."""
        ...

    async def fail(self, message: QueuedMessage) -> bool:
        """Reports a processing failure. Returns True if this was the
        message's last allowed attempt (moved to the dead-letter stream
        and acked off the main one) — False if it remains pending for a
        future retry via claim_next()."""
        ...
