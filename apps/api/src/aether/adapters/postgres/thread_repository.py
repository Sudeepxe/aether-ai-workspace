"""Postgres-backed ThreadRepositoryPort implementation.

Connection-bound (see workspace_repository.py's docstring for why) — used
by the fast, non-streaming thread CRUD/list routes, which share
http/deps.py's ``get_workspace_scope`` one-connection-per-request pattern.
The streaming messages route deliberately does *not* use this — see
ports/chat.py's ``MessageStorePort`` docstring for why holding one
connection open across a token stream would be wrong.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.ports.repositories import Thread


class PostgresThreadRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(
        self, *, id: UUID, workspace_id: UUID, created_by: UUID, title: str | None
    ) -> Thread:
        row = await self._conn.fetchrow(
            """
            INSERT INTO threads (id, workspace_id, created_by, title)
            VALUES ($1, $2, $3, $4)
            RETURNING id, workspace_id, created_by, title, settings,
                      created_at, updated_at, deleted_at
            """,
            id,
            workspace_id,
            created_by,
            title,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_thread(row)

    async def get(self, workspace_id: UUID, thread_id: UUID) -> Thread | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, created_by, title, settings, created_at, updated_at, deleted_at "
            "FROM threads WHERE id = $1 AND workspace_id = $2 AND deleted_at IS NULL",
            thread_id,
            workspace_id,
        )
        return _row_to_thread(row) if row is not None else None

    async def list_by_workspace(
        self, workspace_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Thread]:
        if after is None:
            rows = await self._conn.fetch(
                "SELECT id, workspace_id, created_by, title, settings, created_at, updated_at, deleted_at "
                "FROM threads WHERE workspace_id = $1 AND deleted_at IS NULL "
                "ORDER BY created_at DESC, id DESC LIMIT $2",
                workspace_id,
                limit,
            )
        else:
            after_created_at, after_id = after
            rows = await self._conn.fetch(
                "SELECT id, workspace_id, created_by, title, settings, created_at, updated_at, deleted_at "
                "FROM threads WHERE workspace_id = $1 AND deleted_at IS NULL "
                "AND (created_at, id) < ($2, $3) "
                "ORDER BY created_at DESC, id DESC LIMIT $4",
                workspace_id,
                after_created_at,
                after_id,
                limit,
            )
        return [_row_to_thread(row) for row in rows]

    async def update_title(
        self, workspace_id: UUID, thread_id: UUID, *, title: str | None
    ) -> Thread | None:
        row = await self._conn.fetchrow(
            """
            UPDATE threads SET title = $3, updated_at = now()
            WHERE id = $1 AND workspace_id = $2 AND deleted_at IS NULL
            RETURNING id, workspace_id, created_by, title, settings,
                      created_at, updated_at, deleted_at
            """,
            thread_id,
            workspace_id,
            title,
        )
        return _row_to_thread(row) if row is not None else None

    async def soft_delete(
        self, workspace_id: UUID, thread_id: UUID, *, deleted_at: datetime
    ) -> None:
        await self._conn.execute(
            "UPDATE threads SET deleted_at = $3 WHERE id = $1 AND workspace_id = $2 AND deleted_at IS NULL",
            thread_id,
            workspace_id,
            deleted_at,
        )


def _row_to_thread(row: asyncpg.Record) -> Thread:
    settings: dict[str, Any] = dict(row["settings"])
    return Thread(
        id=row["id"],
        workspace_id=row["workspace_id"],
        created_by=row["created_by"],
        title=row["title"],
        settings=settings,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )
