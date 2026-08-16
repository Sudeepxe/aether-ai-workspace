"""Postgres-backed DocumentRepositoryPort implementation.

Connection-bound (see thread_repository.py's docstring for why) — used
by the API-plane document routes, which share http/deps.py's
``get_workspace_scope`` one-connection-per-request pattern. No manual
``set_config``/``conn.transaction()`` here: the whole request already
runs inside one transaction with ``app.tenant_id`` set once by
``get_workspace_scope``, unlike the worker's pool-per-call adapters.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.repositories import Document, DocumentStatus


class PostgresDocumentRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create_if_absent(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        filename: str,
        content_sha256: str,
        mime: str,
        size_bytes: int,
        object_key: str,
    ) -> Document | None:
        row = await self._conn.fetchrow(
            """
            INSERT INTO documents (
                id, workspace_id, filename, content_sha256, mime, size_bytes, object_key
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            RETURNING id, workspace_id, filename, content_sha256, mime, size_bytes, object_key,
                      status, failure_stage, failure_reason, version, superseded_by,
                      created_at, updated_at, deleted_at
            """,
            id,
            workspace_id,
            filename,
            content_sha256,
            mime,
            size_bytes,
            object_key,
        )
        return _row_to_document(row) if row is not None else None

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, filename, content_sha256, mime, size_bytes, object_key, "
            "status, failure_stage, failure_reason, version, superseded_by, "
            "created_at, updated_at, deleted_at "
            "FROM documents WHERE id = $1 AND workspace_id = $2 AND deleted_at IS NULL",
            document_id,
            workspace_id,
        )
        return _row_to_document(row) if row is not None else None

    async def list_by_workspace(
        self, workspace_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Document]:
        if after is None:
            rows = await self._conn.fetch(
                "SELECT id, workspace_id, filename, content_sha256, mime, size_bytes, object_key, "
                "status, failure_stage, failure_reason, version, superseded_by, "
                "created_at, updated_at, deleted_at "
                "FROM documents WHERE workspace_id = $1 AND deleted_at IS NULL "
                "ORDER BY created_at DESC, id DESC LIMIT $2",
                workspace_id,
                limit,
            )
        else:
            after_created_at, after_id = after
            rows = await self._conn.fetch(
                "SELECT id, workspace_id, filename, content_sha256, mime, size_bytes, object_key, "
                "status, failure_stage, failure_reason, version, superseded_by, "
                "created_at, updated_at, deleted_at "
                "FROM documents WHERE workspace_id = $1 AND deleted_at IS NULL "
                "AND (created_at, id) < ($2, $3) "
                "ORDER BY created_at DESC, id DESC LIMIT $4",
                workspace_id,
                after_created_at,
                after_id,
                limit,
            )
        return [_row_to_document(row) for row in rows]

    async def delete(self, workspace_id: UUID, document_id: UUID, *, deleted_at: datetime) -> bool:
        row = await self._conn.fetchrow(
            "UPDATE documents SET deleted_at = $3, updated_at = $3 "
            "WHERE id = $1 AND workspace_id = $2 AND deleted_at IS NULL RETURNING id",
            document_id,
            workspace_id,
            deleted_at,
        )
        if row is None:
            return False
        # Explicit statement, not a FK cascade (see the port docstring):
        # app_api has SELECT+DELETE on chunks precisely for this — the
        # actual content (chunks + vectors, one column) is gone in the
        # same transaction as the soft-delete above.
        await self._conn.execute("DELETE FROM chunks WHERE document_id = $1", document_id)
        return True


def _row_to_document(row: asyncpg.Record) -> Document:
    return Document(
        id=row["id"],
        workspace_id=row["workspace_id"],
        filename=row["filename"],
        content_sha256=row["content_sha256"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        object_key=row["object_key"],
        status=DocumentStatus(row["status"]),
        failure_stage=row["failure_stage"],
        failure_reason=row["failure_reason"],
        version=row["version"],
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )
