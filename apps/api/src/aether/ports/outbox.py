"""Transactional outbox port (§3.2.9, §8.1) — see the outbox migration's
docstring for the full design rationale."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    tenant_id: UUID | None
    payload: dict[str, Any]
    attempts: int
    created_at: datetime
    dispatched_at: datetime | None


class OutboxRepositoryPort(Protocol):
    async def enqueue(
        self,
        *,
        id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        tenant_id: UUID | None,
        payload: dict[str, Any],
    ) -> None: ...

    async def fetch_pending(
        self, *, event_type: str, max_attempts: int, limit: int
    ) -> list[OutboxEntry]:
        """Rows with ``dispatched_at IS NULL`` and ``attempts < max_attempts``
        — a row that's exhausted its attempts is the dead-letter signal
        (see the migration docstring) and is deliberately excluded here
        so the worker doesn't spin on it forever."""
        ...

    async def mark_dispatched(self, entry_id: UUID, *, dispatched_at: datetime) -> None: ...

    async def record_attempt_failure(self, entry_id: UUID) -> None: ...
