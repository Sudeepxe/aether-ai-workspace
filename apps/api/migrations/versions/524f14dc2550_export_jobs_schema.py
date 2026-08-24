"""export jobs schema

Revision ID: 524f14dc2550
Revises: 6761ab384024
Create Date: 2026-08-24 21:23:51.837891

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

S8 issue #85 (FR-AD-5): the tenant-data-export saga's job-tracking
table (§8.1: same "workspace_id, kind, status, evidence jsonb,
requested_by, completed_at" shape as ``deletion_jobs``). Unlike
``deletion_jobs``, this one keeps a normal FK to ``workspaces`` (ON
DELETE CASCADE) — an export job is a snapshot *of* the workspace, it
has no reason to outlive it.

``archive_object_key`` is a typed column, not buried in ``evidence``:
it's the one field the GET status endpoint must read to mint a
presigned download URL once the saga completes, so it gets first-class
storage rather than a JSON-blob lookup.

Grant additions on tables the worker-plane export saga is the first
consumer of (same "granting nothing until something needs it" posture
as 6761ab384024's grants) — all SELECT-only, this saga only ever reads
tenant data to assemble the archive, never mutates it:
``memberships``, ``threads``, ``messages``, ``message_citations``,
``feedback``. (``documents``, ``usage_events`` already have app_worker
SELECT from earlier sprints.)
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "524f14dc2550"
down_revision: str | None = "6761ab384024"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE export_jobs (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            kind TEXT NOT NULL DEFAULT 'workspace' CHECK (kind IN ('workspace')),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'complete', 'failed')),
            archive_object_key TEXT,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            failure_reason TEXT,
            requested_by UUID NOT NULL REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            CHECK ((status IN ('complete', 'failed')) = (completed_at IS NOT NULL)),
            CHECK ((status = 'failed') = (failure_reason IS NOT NULL)),
            CHECK (status != 'complete' OR archive_object_key IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX export_jobs_workspace_idx ON export_jobs (workspace_id)")

    op.execute("ALTER TABLE export_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE export_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON export_jobs
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    op.execute("GRANT SELECT, INSERT ON export_jobs TO app_api")
    op.execute("GRANT SELECT, UPDATE ON export_jobs TO app_worker")

    # First worker-plane consumers of these tables — see module
    # docstring for the least-privilege reasoning.
    op.execute("GRANT SELECT ON memberships TO app_worker")
    op.execute("GRANT SELECT ON threads TO app_worker")
    op.execute("GRANT SELECT ON messages TO app_worker")
    op.execute("GRANT SELECT ON message_citations TO app_worker")
    op.execute("GRANT SELECT ON feedback TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON feedback FROM app_worker")
    op.execute("REVOKE SELECT ON message_citations FROM app_worker")
    op.execute("REVOKE SELECT ON messages FROM app_worker")
    op.execute("REVOKE SELECT ON threads FROM app_worker")
    op.execute("REVOKE SELECT ON memberships FROM app_worker")
    op.execute("DROP TABLE IF EXISTS export_jobs")
