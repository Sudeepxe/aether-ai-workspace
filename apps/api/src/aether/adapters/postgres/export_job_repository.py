"""Postgres-backed ExportJobRepositoryPort implementation.

Connection-bound (see deletion_job_repository.py's docstring for the
same pattern) — RequestWorkspaceExport composes ``create`` into the
same request transaction as the outbox enqueue, so an export job can
never exist for a request whose outbox write failed, or vice versa.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.repositories import ExportJob, ExportJobStatus


class PostgresExportJobRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(self, *, id: UUID, workspace_id: UUID, requested_by: UUID) -> ExportJob:
        row = await self._conn.fetchrow(
            """
            INSERT INTO export_jobs (id, workspace_id, requested_by)
            VALUES ($1, $2, $3)
            RETURNING id, workspace_id, requested_by, status, archive_object_key, evidence,
                      failure_reason, created_at, updated_at, completed_at
            """,
            id,
            workspace_id,
            requested_by,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row
        return _row_to_job(row)

    async def get_by_id(self, workspace_id: UUID, job_id: UUID) -> ExportJob | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, requested_by, status, archive_object_key, evidence, "
            "failure_reason, created_at, updated_at, completed_at FROM export_jobs "
            "WHERE workspace_id = $1 AND id = $2",
            workspace_id,
            job_id,
        )
        return _row_to_job(row) if row is not None else None


def _row_to_job(row: asyncpg.Record) -> ExportJob:
    return ExportJob(
        id=row["id"],
        workspace_id=row["workspace_id"],
        requested_by=row["requested_by"],
        status=ExportJobStatus(row["status"]),
        archive_object_key=row["archive_object_key"],
        evidence=dict(row["evidence"]),
        failure_reason=row["failure_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )
