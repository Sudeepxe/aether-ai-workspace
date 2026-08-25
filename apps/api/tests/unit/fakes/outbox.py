from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from aether.ports.outbox import OutboxEntry


class FakeOutboxRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, OutboxEntry] = {}

    async def enqueue(
        self,
        *,
        id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        tenant_id: UUID | None,
        payload: dict[str, Any],
    ) -> None:
        self._rows[id] = OutboxEntry(
            id=id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            tenant_id=tenant_id,
            payload=payload,
            attempts=0,
            created_at=datetime.now().astimezone(),
            dispatched_at=None,
        )

    async def fetch_pending(
        self, *, event_type: str, max_attempts: int, limit: int
    ) -> list[OutboxEntry]:
        matches = [
            entry
            for entry in self._rows.values()
            if entry.event_type == event_type
            and entry.dispatched_at is None
            and entry.attempts < max_attempts
        ]
        return matches[:limit]

    async def mark_dispatched(self, entry_id: UUID, *, dispatched_at: datetime) -> None:
        self._rows[entry_id] = replace(self._rows[entry_id], dispatched_at=dispatched_at)

    async def record_attempt_failure(self, entry_id: UUID) -> None:
        current = self._rows[entry_id]
        self._rows[entry_id] = replace(current, attempts=current.attempts + 1)
