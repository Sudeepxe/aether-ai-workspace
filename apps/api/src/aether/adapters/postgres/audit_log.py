"""Postgres-backed AuditLogPort implementation (FR-AD-1, §3.7.3).

Accepts either a bare Pool or a Connection. Workspace-scoped mutations
pass the request's already-open, tenant-scoped Connection, so the audit
write commits or rolls back atomically with the business mutation it
records. Auth-plane events (registration, login, logout) have no
workspace — a fresh pool-acquired connection has never had
app.tenant_id set, so current_setting(...) returns SQL NULL, which
satisfies audit_events' `workspace_id IS NOT DISTINCT FROM NULL` policy
with no explicit SET LOCAL needed; the singleton, pool-bound instance in
Container is used for exactly that case.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.ports.audit import AuditEvent


class PostgresAuditLog:
    def __init__(self, conn: asyncpg.Pool | asyncpg.Connection) -> None:
        self._conn = conn

    async def record(
        self,
        *,
        id: UUID,
        workspace_id: UUID | None,
        actor_user_id: UUID | None,
        actor_key_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict[str, Any],
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO audit_events
                (id, workspace_id, actor_user_id, actor_key_id, action, target_type, target_id, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            id,
            workspace_id,
            actor_user_id,
            actor_key_id,
            action,
            target_type,
            target_id,
            metadata,
        )

    async def list_by_workspace(
        self, workspace_id: UUID, *, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[AuditEvent]:
        if cursor is None:
            rows = await self._conn.fetch(
                """
                SELECT id, workspace_id, actor_user_id, actor_key_id, action, target_type,
                       target_id, metadata, occurred_at
                FROM audit_events WHERE workspace_id = $1
                ORDER BY occurred_at DESC, id DESC LIMIT $2
                """,
                workspace_id,
                limit,
            )
        else:
            cursor_occurred_at, cursor_id = cursor
            rows = await self._conn.fetch(
                """
                SELECT id, workspace_id, actor_user_id, actor_key_id, action, target_type,
                       target_id, metadata, occurred_at
                FROM audit_events
                WHERE workspace_id = $1 AND (occurred_at, id) < ($2, $3)
                ORDER BY occurred_at DESC, id DESC LIMIT $4
                """,
                workspace_id,
                cursor_occurred_at,
                cursor_id,
                limit,
            )
        return [_row_to_audit_event(row) for row in rows]


def _row_to_audit_event(row: asyncpg.Record) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        workspace_id=row["workspace_id"],
        actor_user_id=row["actor_user_id"],
        actor_key_id=row["actor_key_id"],
        action=row["action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        metadata=dict(row["metadata"]),
        occurred_at=row["occurred_at"],
    )
