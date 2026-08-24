"""deletion verification schema

Revision ID: 2cf76e54c64e
Revises: 524f14dc2550
Create Date: 2026-08-24 23:35:28.203792

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

S8 issue #86 (NFR-PR-1, §11.6's exit criterion): an independent
verification pass over completed ``deletion_jobs`` rows, distinct from
the deletion saga's own self-reported ``status``. ``verified_at``/
``verification_passed`` are nullable — NULL means "not yet verified",
never a silent default of "assumed clean". The partial index only
covers the sweep's actual query shape (completed, unverified jobs),
staying small regardless of total deletion_jobs volume.

Two grant additions — first worker-plane reads of these two tables,
needed for the verifier's real, independent residue sweep (it must be
able to see genuine leftover rows if the deletion saga's own cascade
had a bug, not just trust that "nothing references this workspace_id
because RLS says so" — RLS scoped to the same tenant_id would show real
residue exactly as it should):
- ``GRANT SELECT ON invitations TO app_worker`` — invitations is RLS-
  exempt (see its own migration), so no ``set_config`` needed, just the
  grant itself.
- ``GRANT SELECT ON memory_summaries TO app_worker``.

One additional, role-scoped policy on ``deletion_jobs`` itself: the
verification sweep must list pending jobs *across every workspace* (it
doesn't know in advance which tenant a completed-but-unverified job
belongs to — that's the whole point of a periodic sweep), but the
table's existing ``tenant_isolation`` policy scopes visibility to
whatever ``app.tenant_id`` happens to be set, which is fundamentally
incompatible with a cross-tenant listing query. Rather than exempt the
whole table from RLS (``outbox``'s pattern, but that table has no
per-row tenant concept to protect at all — this one does, for every
other caller), this adds a second, ``app_worker``-only, SELECT-only
permissive policy. Postgres combines multiple permissive policies for
the same command with OR, so: app_worker's SELECTs are unconditionally
visible (needed for the sweep's listing query); every other role, and
every other command (INSERT/UPDATE/DELETE) for app_worker itself, still
goes through the original tenant-scoped policy unchanged — the worker's
own mark_running/complete/record_verification calls already set
app.tenant_id correctly before writing, so they need no bypass.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2cf76e54c64e"
down_revision: str | None = "524f14dc2550"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE deletion_jobs ADD COLUMN verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE deletion_jobs ADD COLUMN verification_passed BOOLEAN")
    op.execute(
        "CREATE INDEX deletion_jobs_pending_verification_idx ON deletion_jobs (completed_at) "
        "WHERE status = 'complete' AND verified_at IS NULL"
    )
    op.execute(
        """
        CREATE POLICY worker_verification_read ON deletion_jobs
            FOR SELECT TO app_worker USING (true)
        """
    )

    op.execute("GRANT SELECT ON invitations TO app_worker")
    op.execute("GRANT SELECT ON memory_summaries TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON memory_summaries FROM app_worker")
    op.execute("REVOKE SELECT ON invitations FROM app_worker")
    op.execute("DROP POLICY IF EXISTS worker_verification_read ON deletion_jobs")
    op.execute("DROP INDEX IF EXISTS deletion_jobs_pending_verification_idx")
    op.execute("ALTER TABLE deletion_jobs DROP COLUMN IF EXISTS verification_passed")
    op.execute("ALTER TABLE deletion_jobs DROP COLUMN IF EXISTS verified_at")
