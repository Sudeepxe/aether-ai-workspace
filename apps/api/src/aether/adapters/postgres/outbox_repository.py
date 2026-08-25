"""Postgres-backed OutboxRepositoryPort implementation.

Accepts either a bare Pool or a Connection: the producer side
(``enqueue``) is called from within an already-open, tenant-scoped
transaction (so the outbox row commits or rolls back with the business
mutation it accompanies); the worker's consumer side runs standalone
against the pool, dispatching across every tenant by design (see the
migration's docstring for why this table has no RLS).

``enqueue_idempotent`` exists as a distinct method rather than folding
``ON CONFLICT (id) DO NOTHING`` into ``enqueue`` itself: Postgres
requires SELECT privilege (not just INSERT) to evaluate an ON CONFLICT
arbiter, even for DO NOTHING — app_api's outbox grant is INSERT-only
(password reset, invitations always pass a fresh id, so idempotency
never mattered for them), and broadening it to SELECT just to support
issue #47's worker-plane use case would violate least privilege for a
role that has no reason to ever read outbox rows back. app_worker
already has SELECT (the dispatcher's read side), so only its own
document.ready/document.failed writes — the ones that genuinely need a
deterministic, redelivery-safe id — use this method.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.observability.tracing import inject_trace_context
from aether.ports.outbox import OutboxEntry
from aether.ports.outbox_metrics import OutboxStats


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
            INSERT INTO outbox (id, aggregate_type, aggregate_id, event_type, tenant_id, payload, trace_context)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            id,
            aggregate_type,
            aggregate_id,
            event_type,
            tenant_id,
            payload,
            inject_trace_context(),
        )

    async def enqueue_idempotent(
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
            INSERT INTO outbox (id, aggregate_type, aggregate_id, event_type, tenant_id, payload, trace_context)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            """,
            id,
            aggregate_type,
            aggregate_id,
            event_type,
            tenant_id,
            payload,
            inject_trace_context(),
        )

    async def fetch_pending(
        self, *, event_type: str, max_attempts: int, limit: int
    ) -> list[OutboxEntry]:
        rows = await self._conn.fetch(
            """
            SELECT id, aggregate_type, aggregate_id, event_type, tenant_id, payload,
                   attempts, created_at, dispatched_at, trace_context
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

    async def get_stats(self, *, event_type: str, max_attempts: int) -> OutboxStats:
        """Not part of ``OutboxRepositoryPort`` — see
        ``ports.outbox_metrics``'s docstring for why this lives as an
        extra method on the concrete adapter instead of growing that
        shared Protocol."""
        row = await self._conn.fetchrow(
            """
            SELECT
                EXTRACT(EPOCH FROM (now() - MIN(created_at) FILTER (
                    WHERE dispatched_at IS NULL AND attempts < $2
                ))) AS oldest_pending_seconds,
                COUNT(*) FILTER (
                    WHERE dispatched_at IS NULL AND attempts >= $2
                ) AS dlq_depth
            FROM outbox
            WHERE event_type = $1
            """,
            event_type,
            max_attempts,
        )
        assert row is not None  # noqa: S101 — COUNT/EXTRACT always return exactly one row
        oldest = row["oldest_pending_seconds"]
        return OutboxStats(
            oldest_pending_seconds=float(oldest) if oldest is not None else None,
            dlq_depth=row["dlq_depth"],
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
        trace_context=dict(row["trace_context"]),
    )
