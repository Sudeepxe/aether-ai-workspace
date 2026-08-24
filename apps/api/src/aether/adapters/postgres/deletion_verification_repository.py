"""Postgres-backed DeletionVerificationPort implementation (NFR-PR-1,
issue #86).

Pool-bound, mirroring workspace_deletion_repository.py's shape. Every
count in ``count_residual_rows`` is a real, independent query against
the actual table — none of it is derived from ``deletion_jobs.evidence``
or any other value the deletion saga itself already computed, which is
the entire point of this being a *verification* pass rather than a
re-display of the deletion job's own self-report.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.deletion_verification import DeletionJob, DeletionJobStatus, ResidueReport

# Every tenant-scoped table with a workspace_id column, excluding
# audit_events (checked separately below — a NULL-workspace system-
# plane row is a legitimate survivor, not residue) and deletion_jobs
# itself (the evidence record, not something it's residue of).
_RESIDUE_TABLES = (
    "memberships",
    "threads",
    "messages",
    "message_citations",
    "documents",
    "chunks",
    "usage_events",
    "budgets",
    "invitations",
    "feedback",
    "memory_summaries",
)


class PostgresDeletionVerificationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_jobs_pending_verification(
        self, *, min_age_seconds: int, limit: int
    ) -> list[DeletionJob]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, workspace_id, requested_by, status, evidence, failure_reason, "
                "created_at, updated_at, completed_at, verified_at, verification_passed "
                "FROM deletion_jobs WHERE status = 'complete' AND verified_at IS NULL "
                "AND completed_at <= now() - make_interval(secs => $1) "
                "ORDER BY completed_at LIMIT $2",
                min_age_seconds,
                limit,
            )
            return [_row_to_job(row) for row in rows]

    async def count_residual_rows(self, workspace_id: UUID) -> dict[str, int]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            residual: dict[str, int] = {}
            for table in _RESIDUE_TABLES:
                # table is always one of the fixed literals above, never
                # user input — safe string interpolation, not injectable.
                count = await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE workspace_id = $1",  # noqa: S608
                    workspace_id,
                )
                if count > 0:
                    residual[table] = count
            audit_count = await conn.fetchval(
                "SELECT count(*) FROM audit_events WHERE workspace_id = $1", workspace_id
            )
            if audit_count > 0:
                residual["audit_events"] = audit_count
            return residual

    async def record_verification(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        report: ResidueReport,
        verified_at: datetime,
        audit_event_id: UUID,
    ) -> None:
        verification = {
            "passed": report.passed,
            "residual_rows": report.residual_rows,
            "residual_object_count": len(report.residual_object_keys),
        }
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE deletion_jobs SET verified_at = $2, verification_passed = $3, "
                "evidence = evidence || jsonb_build_object('verification', $4::jsonb) "
                "WHERE id = $1 AND workspace_id = $5",
                job_id,
                verified_at,
                report.passed,
                verification,
                workspace_id,
            )
            # Reset tenant context before the audit write — same NULL-
            # workspace, system-plane event shape as the deletion saga's
            # own completion event (see workspace_deletion_repository.py's
            # docstring): this survives forever, not cascaded away.
            await conn.execute("SELECT set_config('app.tenant_id', '', true)")
            await conn.execute(
                """
                INSERT INTO audit_events
                    (id, workspace_id, actor_user_id, actor_key_id, action, target_type,
                     target_id, metadata)
                VALUES ($1, NULL, NULL, NULL, 'workspace.deletion_verified', 'workspace', $2, $3)
                """,
                audit_event_id,
                workspace_id,
                verification,
            )


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
