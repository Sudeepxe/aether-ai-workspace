"""Postgres-backed OutboxRepositoryPort implementation.

Accepts either a bare Pool or a Connection: the producer side
(``enqueue``) is called from within an already-open, tenant-scoped
transaction (so the outbox row commits or rolls back with the business
mutation it accompanies); the worker's consumer side runs standalone
against the pool, dispatching across every tenant by design (see the
migration's docstring for why this table has no RLS).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.ports.outbox import OutboxEntry


class PostgresOutboxRepository:
    def __init__(self, conn: asyncpg.Pool | asyncpg.Connection) -> None:
        self._conn = conn

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
        await self._conn.execute(
            """
            INSERT INTO outbox (id, aggregate_type, aggregate_id, event_type, tenant_id, payload)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            id,
            aggregate_type,
            aggregate_id,
            event_type,
            tenant_id,
            payload,
        )

    async def fetch_pending(
        self, *, event_type: str, max_attempts: int, limit: int
    ) -> list[OutboxEntry]:
        rows = await self._conn.fetch(
            """
            SELECT id, aggregate_type, aggregate_id, event_type, tenant_id, payload,
                   attempts, created_at, dispatched_at
            FROM outbox
            WHERE event_type = $1 AND dispatched_at IS NULL AND attempts < $2
            ORDER BY created_at
            LIMIT $3
            """,
            event_type,
            max_attempts,
            limit,
        )
        return [_row_to_entry(row) for row in rows]

    async def mark_dispatched(self, entry_id: UUID, *, dispatched_at: datetime) -> None:
        await self._conn.execute(
            "UPDATE outbox SET dispatched_at = $2 WHERE id = $1", entry_id, dispatched_at
        )

    async def record_attempt_failure(self, entry_id: UUID) -> None:
        await self._conn.execute(
            "UPDATE outbox SET attempts = attempts + 1 WHERE id = $1", entry_id
        )


def _row_to_entry(row: asyncpg.Record) -> OutboxEntry:
    return OutboxEntry(
        id=row["id"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        event_type=row["event_type"],
        tenant_id=row["tenant_id"],
        payload=dict(row["payload"]),
        attempts=row["attempts"],
        created_at=row["created_at"],
        dispatched_at=row["dispatched_at"],
    )
