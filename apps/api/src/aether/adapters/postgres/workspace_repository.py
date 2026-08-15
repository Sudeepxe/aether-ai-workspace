"""Postgres-backed WorkspaceRepositoryPort implementation.

Unlike Sprint 1's user/refresh-token repos, this one is bound to a single
``asyncpg.Connection``, not a ``Pool`` — workspace-scoped operations run
inside a per-request transaction with ``SET LOCAL app.tenant_id`` already
applied (§3.7.2 layer 4), so every query in the request must share that
one transaction/connection, not a fresh connection per call from the
pool. See http/deps.py's ``get_tenant_connection``, the composition point
for this.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.ports.repositories import Workspace


class PostgresWorkspaceRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        id: UUID,
        name: str,
        slug: str,
        settings: dict[str, Any],
        model_policy: dict[str, Any],
    ) -> Workspace:
        row = await self._conn.fetchrow(
            """
            INSERT INTO workspaces (id, name, slug, settings, model_policy)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, slug, settings, model_policy, created_at, updated_at, deleted_at
            """,
            id,
            name,
            slug,
            settings,
            model_policy,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_workspace(row)

    async def get_by_id(self, workspace_id: UUID) -> Workspace | None:
        row = await self._conn.fetchrow(
            "SELECT id, name, slug, settings, model_policy, created_at, updated_at, deleted_at "
            "FROM workspaces WHERE id = $1 AND deleted_at IS NULL",
            workspace_id,
        )
        return _row_to_workspace(row) if row is not None else None

    async def update(
        self,
        workspace_id: UUID,
        *,
        name: str,
        settings: dict[str, Any],
        model_policy: dict[str, Any],
        expected_updated_at: datetime,
    ) -> Workspace | None:
        row = await self._conn.fetchrow(
            """
            UPDATE workspaces
            SET name = $2, settings = $3, model_policy = $4, updated_at = now()
            WHERE id = $1 AND updated_at = $5 AND deleted_at IS NULL
            RETURNING id, name, slug, settings, model_policy, created_at, updated_at, deleted_at
            """,
            workspace_id,
            name,
            settings,
            model_policy,
            expected_updated_at,
        )
        return _row_to_workspace(row) if row is not None else None

    async def soft_delete(self, workspace_id: UUID, *, deleted_at: datetime) -> None:
        await self._conn.execute(
            "UPDATE workspaces SET deleted_at = $2 WHERE id = $1 AND deleted_at IS NULL",
            workspace_id,
            deleted_at,
        )


def _row_to_workspace(row: asyncpg.Record) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        settings=dict(row["settings"]),
        model_policy=dict(row["model_policy"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )
