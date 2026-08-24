"""Postgres-backed WorkspaceDeletionPort implementation (DF-3, issue #84).

Pool-bound (see message_store.py's docstring for the same pattern) —
each method opens its own short-lived transaction rather than the
handler holding one open across the saga's slow object-storage I/O.
Every method sets ``app.tenant_id`` itself (transaction-local,
per-call) since ``deletion_jobs``/``documents`` are both RLS-scoped and
this adapter is called from a pool, never from an already-tenant-scoped
connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.ports.workspace_deletion import DeletionJob, DeletionJobStatus


class PostgresWorkspaceDeletionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_job(self, workspace_id: UUID, job_id: UUID) -> DeletionJob | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            row = await conn.fetchrow(
                "SELECT id, workspace_id, requested_by, status, evidence, failure_reason, "
                "created_at, updated_at, completed_at, verified_at, verification_passed "
                "FROM deletion_jobs WHERE id = $1 AND workspace_id = $2",
                job_id,
                workspace_id,
            )
            return _row_to_job(row) if row is not None else None

    async def mark_running(self, workspace_id: UUID, job_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE deletion_jobs SET status = 'running', updated_at = now() "
                "WHERE id = $1 AND workspace_id = $2 AND status = 'queued'",
                job_id,
                workspace_id,
            )

    async def list_object_keys(self, workspace_id: UUID) -> list[str]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            # Every document's key regardless of deleted_at — see the
            # port's docstring: an individually soft-deleted document's
            # bytes were never purged (nothing consumes document.deleted
            # today), so a workspace deletion is the first point any of
            # them are provably removed.
            rows = await conn.fetch(
                "SELECT DISTINCT object_key FROM documents WHERE workspace_id = $1", workspace_id
            )
            return [row["object_key"] for row in rows]

    async def complete(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        audit_event_id: UUID,
        evidence: dict[str, Any],
        completed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE deletion_jobs SET status = 'complete', evidence = $2, "
                "completed_at = $3, updated_at = $3 WHERE id = $1 AND workspace_id = $4",
                job_id,
                evidence,
                completed_at,
                workspace_id,
            )
            # Reset tenant context before the audit write: audit_events'
            # policy is workspace_id IS NOT DISTINCT FROM app.tenant_id,
            # and this row is deliberately a NULL-workspace, system-plane
            # event (see ports.workspace_deletion's docstring) — the same
            # shape auth-plane events use, chosen so the evidence survives
            # the hard-delete below rather than cascading away with it.
            await conn.execute("SELECT set_config('app.tenant_id', '', true)")
            await conn.execute(
                """
                INSERT INTO audit_events
                    (id, workspace_id, actor_user_id, actor_key_id, action, target_type,
                     target_id, metadata)
                VALUES ($1, NULL, NULL, NULL, 'workspace.deleted', 'workspace', $2, $3)
                """,
                audit_event_id,
                workspace_id,
                evidence,
            )
            # Cascades every remaining child table (memberships, threads,
            # messages, message_citations, documents, chunks, usage_events,
            # budgets, invitations, feedback, memory_summaries) in this
            # same transaction — deletion_jobs has no FK here by design,
            # so it (and the audit row above) survive this statement.
            await conn.execute("DELETE FROM workspaces WHERE id = $1", workspace_id)


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
        verified_at=row["verified_at"],
        verification_passed=row["verification_passed"],
    )
