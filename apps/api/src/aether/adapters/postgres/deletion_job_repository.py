"""Postgres-backed DeletionJobRepositoryPort implementation.

Connection-bound (see citation_repository.py's docstring for the same
pattern) — DeleteWorkspace composes ``create`` into the same transaction
as the workspace's soft-delete and the outbox enqueue, so a deletion
job can never exist for a request that failed to soft-delete, or
vice versa.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.repositories import DeletionJob, DeletionJobStatus


class PostgresDeletionJobRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(self, *, id: UUID, workspace_id: UUID, requested_by: UUID) -> DeletionJob:
        row = await self._conn.fetchrow(
            """
            INSERT INTO deletion_jobs (id, workspace_id, requested_by)
            VALUES ($1, $2, $3)
            RETURNING id, workspace_id, requested_by, status, evidence, failure_reason,
                      created_at, updated_at, completed_at
            """,
            id,
            workspace_id,
            requested_by,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row
        return _row_to_job(row)

    async def get_by_id(self, workspace_id: UUID, job_id: UUID) -> DeletionJob | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, requested_by, status, evidence, failure_reason, "
            "created_at, updated_at, completed_at FROM deletion_jobs "
            "WHERE workspace_id = $1 AND id = $2",
            workspace_id,
            job_id,
        )
        return _row_to_job(row) if row is not None else None


def _row_to_job(row: asyncpg.Record) -> DeletionJob:
    return DeletionJob(
        id=row["id"],
        workspace_id=row["workspace_id"],
        requested_by=row["requested_by"],
        status=DeletionJobStatus(row["status"]),
        evidence=dict(row["evidence"]),
        failure_reason=row["failure_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )
