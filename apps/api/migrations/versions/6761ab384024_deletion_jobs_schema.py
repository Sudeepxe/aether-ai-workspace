"""deletion jobs schema

Revision ID: 6761ab384024
Revises: 14494bfcbc14
Create Date: 2026-08-24 20:57:02.987439

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

S8 issue #84 (DF-3): the async workspace-deletion saga's job-tracking
table (§8.1: "workspace_id, kind, status, evidence jsonb, requested_by,
completed_at"). Every other tenant-scoped table's ``workspace_id`` FKs
to ``workspaces(id) ON DELETE CASCADE`` — this one deliberately does
NOT: the saga's own final step hard-deletes the ``workspaces`` row, and
an FK'd job row would be destroyed by the very cascade it's supposed to
record evidence of (the same "no FK yet" posture already used for
``audit_events.actor_key_id`` and ``invitations``' RLS exemption —
neither is a table this repo is willing to complicate its FK graph for
prematurely). RLS still applies (workspace_id is a plain column check,
independent of FK presence): while the workspace exists, only a session
scoped to it can see its job rows; after the hard-delete, no ordinary
request can ever set ``app.tenant_id`` to that workspace's id again
(its membership rows are gone too), so the row becomes admin/bootstrap-
readable evidence only — not a bug, the intended shape for durable
deletion evidence that outlives the thing it documents.

Two grant additions on existing tables, both because this is the first
worker-plane consumer to ever touch them (37dc74e7cc34's original
grants comment: "app_worker: no grants on identity/tenancy tables in
Sprint 1... granting nothing until something needs it" — this is that
something):
- ``GRANT SELECT, DELETE ON workspaces TO app_worker`` — the saga's own
  hard-delete step.
- ``GRANT SELECT, INSERT ON audit_events TO app_worker`` — the saga
  writes its own ``workspace.deleted`` completion evidence event
  (workspace_id=NULL, a system-plane row, written from a tenant-
  context-free point in the same transaction — see
  ports/workspace_deletion.py's docstring), mirroring app_api's
  INSERT-only, no-UPDATE/DELETE audit-tampering posture (§3.7.3).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6761ab384024"
down_revision: str | None = "14494bfcbc14"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE deletion_jobs (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            kind TEXT NOT NULL DEFAULT 'workspace' CHECK (kind IN ('workspace')),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'complete', 'failed')),
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            failure_reason TEXT,
            requested_by UUID NOT NULL REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            CHECK ((status IN ('complete', 'failed')) = (completed_at IS NOT NULL)),
            CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX deletion_jobs_workspace_idx ON deletion_jobs (workspace_id)")

    op.execute("ALTER TABLE deletion_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deletion_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON deletion_jobs
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # app_api: creates the row (DeleteWorkspace) and reads it back for
    # status polling — never updates or deletes (the saga's own worker-
    # plane transitions are the only legitimate writer of status/evidence).
    op.execute("GRANT SELECT, INSERT ON deletion_jobs TO app_api")
    # app_worker: advances status/evidence through the saga.
    op.execute("GRANT SELECT, UPDATE ON deletion_jobs TO app_worker")

    # First worker-plane consumers of these two tables — see module
    # docstring for the least-privilege reasoning.
    op.execute("GRANT SELECT, DELETE ON workspaces TO app_worker")
    op.execute("GRANT SELECT, INSERT ON audit_events TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON audit_events FROM app_worker")
    op.execute("REVOKE SELECT, DELETE ON workspaces FROM app_worker")
    op.execute("DROP TABLE IF EXISTS deletion_jobs")
