"""Postgres-backed WorkspaceExportPort implementation (FR-AD-5, issue #85).

Pool-bound, mirroring workspace_deletion_repository.py's shape and
per-call transaction-local ``app.tenant_id`` pattern exactly. Writes raw
SQL directly rather than reusing the API-plane Postgres*Repository
adapters (which would be a real, tested-code-reuse win) because
import-linter's "adapters depend only on ports" contract forbids one
adapter importing another — the same constraint every other worker-
plane repository in this codebase already lives with.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from aether.ports.workspace_export import (
    ExportedCitation,
    ExportedDocument,
    ExportedFeedback,
    ExportedMembership,
    ExportedMessage,
    ExportedThread,
    ExportJob,
    ExportJobStatus,
    WorkspaceExportData,
)


class PostgresWorkspaceExportRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_job(self, workspace_id: UUID, job_id: UUID) -> ExportJob | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            row = await conn.fetchrow(
                "SELECT id, workspace_id, requested_by, status, archive_object_key, evidence, "
                "failure_reason, created_at, updated_at, completed_at FROM export_jobs "
                "WHERE id = $1 AND workspace_id = $2",
                job_id,
                workspace_id,
            )
            return _row_to_job(row) if row is not None else None

    async def mark_running(self, workspace_id: UUID, job_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE export_jobs SET status = 'running', updated_at = now() "
                "WHERE id = $1 AND workspace_id = $2 AND status = 'queued'",
                job_id,
                workspace_id,
            )

    async def fetch_export_data(self, workspace_id: UUID) -> WorkspaceExportData:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))

            workspace_row = await conn.fetchrow(
                "SELECT id, name, slug, created_at FROM workspaces WHERE id = $1", workspace_id
            )
            assert workspace_row is not None  # noqa: S101 — the requesting saga already validated it

            membership_rows = await conn.fetch(
                "SELECT user_id, role, created_at FROM memberships WHERE workspace_id = $1",
                workspace_id,
            )

            thread_rows = await conn.fetch(
                "SELECT id, title, created_at FROM threads WHERE workspace_id = $1", workspace_id
            )
            threads: list[ExportedThread] = []
            for thread_row in thread_rows:
                message_rows = await conn.fetch(
                    "SELECT id, seq, role, content, grounded, created_at FROM messages "
                    "WHERE workspace_id = $1 AND thread_id = $2 ORDER BY seq",
                    workspace_id,
                    thread_row["id"],
                )
                message_ids = [m["id"] for m in message_rows if m["grounded"]]
                citations_by_message: dict[UUID, list[ExportedCitation]] = {}
                if message_ids:
                    citation_rows = await conn.fetch(
                        "SELECT message_id, document_title, section_path, page_start, page_end "
                        "FROM message_citations "
                        "WHERE workspace_id = $1 AND message_id = ANY($2::uuid[])",
                        workspace_id,
                        message_ids,
                    )
                    for c in citation_rows:
                        citations_by_message.setdefault(c["message_id"], []).append(
                            ExportedCitation(
                                document_title=c["document_title"],
                                section_path=c["section_path"],
                                page_start=c["page_start"],
                                page_end=c["page_end"],
                            )
                        )
                threads.append(
                    ExportedThread(
                        id=thread_row["id"],
                        title=thread_row["title"],
                        created_at=thread_row["created_at"],
                        messages=[
                            ExportedMessage(
                                id=m["id"],
                                seq=m["seq"],
                                role=m["role"],
                                content=m["content"],
                                grounded=m["grounded"],
                                created_at=m["created_at"],
                                citations=citations_by_message.get(m["id"], []),
                            )
                            for m in message_rows
                        ],
                    )
                )

            document_rows = await conn.fetch(
                "SELECT id, filename, mime, size_bytes, status, object_key, created_at "
                "FROM documents WHERE workspace_id = $1",
                workspace_id,
            )
            feedback_rows = await conn.fetch(
                "SELECT message_id, user_id, rating, reason, created_at "
                "FROM feedback WHERE workspace_id = $1",
                workspace_id,
            )
            usage_row = await conn.fetchrow(
                # ::bigint cast matters: SUM() over a bigint column
                # returns numeric in Postgres, which asyncpg maps to
                # Python Decimal — not JSON-serializable as-is, unlike
                # the plain int COUNT(*) already yields.
                "SELECT COALESCE(SUM(cost_microcents), 0)::bigint AS total_cost_microcents, "
                "COUNT(*) AS request_count FROM usage_events WHERE workspace_id = $1",
                workspace_id,
            )
            assert usage_row is not None  # noqa: S101 — COUNT/SUM always return exactly one row

            return WorkspaceExportData(
                workspace_id=workspace_row["id"],
                workspace_name=workspace_row["name"],
                workspace_slug=workspace_row["slug"],
                workspace_created_at=workspace_row["created_at"],
                memberships=[
                    ExportedMembership(
                        user_id=m["user_id"], role=m["role"], created_at=m["created_at"]
                    )
                    for m in membership_rows
                ],
                threads=threads,
                documents=[
                    ExportedDocument(
                        id=d["id"],
                        filename=d["filename"],
                        mime=d["mime"],
                        size_bytes=d["size_bytes"],
                        status=d["status"],
                        object_key=d["object_key"],
                        created_at=d["created_at"],
                    )
                    for d in document_rows
                ],
                feedback=[
                    ExportedFeedback(
                        message_id=f["message_id"],
                        user_id=f["user_id"],
                        rating=f["rating"],
                        reason=f["reason"],
                        created_at=f["created_at"],
                    )
                    for f in feedback_rows
                ],
                usage_total_cost_microcents=usage_row["total_cost_microcents"],
                usage_request_count=usage_row["request_count"],
            )

    async def complete(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        archive_object_key: str,
        evidence: dict[str, Any],
        completed_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE export_jobs SET status = 'complete', archive_object_key = $2, "
                "evidence = $3, completed_at = $4, updated_at = $4 "
                "WHERE id = $1 AND workspace_id = $5",
                job_id,
                archive_object_key,
                evidence,
                completed_at,
                workspace_id,
            )


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
