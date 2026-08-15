"""tenancy crud and audit log schema

Revision ID: 9b9e2d39ed9a
Revises: 37dc74e7cc34
Create Date: 2026-08-15 19:47:45.010646

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Sprint 2 slice of the §8.1 schema catalog:

- ``invitations`` — single-use, expiring workspace invitations (FR-ID-3).
  Deliberately RLS-exempt, not the ``memberships`` pattern — see the
  comment at its GRANT statement below for why (the accept-by-token flow
  has no tenant context to set: the caller isn't a member yet).
- ``audit_events`` — the immutable audit log (FR-AD-1, §8.1). Monthly
  range partitions from day one (ADR-8.3); ``app_api`` gets SELECT+INSERT
  only, no UPDATE/DELETE grant at all, so tampering by the running
  application is impossible at the privilege level, not just discouraged
  by application code (§3.7.3 "audit tampering" threat row). RLS applies
  to a partitioned parent's policies automatically across all its
  partitions (PG 11+) — one policy, not one per partition.
  ``actor_key_id`` has no FK yet: ``api_keys`` doesn't exist until a
  later sprint; it's a bare nullable UUID column until then, matching
  the "don't invent premature dependencies" discipline used elsewhere.
  Only the current and next month's partitions are created here —
  ongoing partition provisioning is an operational task, tracked as a
  known gap (ADR-8.3 already documents the same posture for messages/
  chunks: "a migration path is documented for the 100x story", not
  automated on day one).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9b9e2d39ed9a"
down_revision: str | None = "37dc74e7cc34"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # --- invitations (FR-ID-3) ---------------------------------------------
    op.execute(
        """
        CREATE TABLE invitations (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            email CITEXT NOT NULL,
            role membership_role NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            invited_by UUID NOT NULL REFERENCES users(id),
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX invitations_workspace_id_idx ON invitations (workspace_id)")

    # Deliberately RLS-exempt, not the memberships pattern — invitations
    # are *accepted* by a caller who is, by definition, not yet a member
    # of the workspace, so no tenant context can exist at the point the
    # accept flow needs to look a token up by its hash (the identical
    # chicken-and-egg reason refresh_tokens has no RLS either: it's
    # looked up by hash before any session exists). The token itself
    # (128-bit, single-use) is the security boundary for that lookup, not
    # tenant context. Admin-facing operations (create/list/revoke) always
    # know workspace_id from the URL and enforce it as an explicit typed
    # repository parameter (§3.7.2 layer 3) instead of relying on RLS.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO app_api")

    # --- audit_events (FR-AD-1, ADR-8.3) ------------------------------------
    op.execute(
        """
        CREATE TABLE audit_events (
            id UUID NOT NULL,
            workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
            actor_user_id UUID REFERENCES users(id),
            actor_key_id UUID,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id UUID NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
        """
    )
    op.execute(
        "CREATE INDEX audit_events_workspace_occurred_idx "
        "ON audit_events (workspace_id, occurred_at)"
    )

    # Current + next month only — see module docstring re: ongoing
    # partition provisioning being a tracked operational gap, not
    # automated here.
    op.execute(
        """
        CREATE TABLE audit_events_2026_08 PARTITION OF audit_events
            FOR VALUES FROM ('2026-08-01') TO ('2026-09-01')
        """
    )
    op.execute(
        """
        CREATE TABLE audit_events_2026_09 PARTITION OF audit_events
            FOR VALUES FROM ('2026-09-01') TO ('2026-10-01')
        """
    )

    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    # audit_events.workspace_id is nullable by design (system/auth-plane
    # events — e.g. login — have no tenant context to record; §8.1:
    # "workspace null for system-level events"). `=` is never true for
    # NULL, so memberships'/invitations' plain-equality policy would make
    # it impossible to ever write or correctly scope those rows: a NULL
    # workspace_id would fail WITH CHECK even under a NULL (context-free)
    # tenant setting. IS NOT DISTINCT FROM treats NULL-vs-NULL as a match,
    # so: a real tenant context sees/writes only that tenant's rows (NULL
    # rows excluded — still fails safe, no cross-tenant leakage); a
    # context-free session (no SET LOCAL app.tenant_id — the auth-plane
    # case) sees/writes only the NULL-workspace system rows, never any
    # tenant's data.
    op.execute(
        """
        CREATE POLICY tenant_isolation ON audit_events
            USING (
                workspace_id IS NOT DISTINCT FROM
                NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                workspace_id IS NOT DISTINCT FROM
                NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
        """
    )

    # INSERT-only by grant — no UPDATE, no DELETE. This is the actual
    # enforcement mechanism (§3.7.3: "audit tampering... INSERT-only
    # grants"), not an application-code convention.
    op.execute("GRANT SELECT, INSERT ON audit_events TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS invitations")
