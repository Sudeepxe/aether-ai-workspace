"""api keys schema

Revision ID: d5ca40686afd
Revises: b5f9a5f82856
Create Date: 2026-08-25 16:35:25.521480

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

S10 (FR-API-2, §7.4): workspace-scoped, hashed, scoped, revocable API
keys — the last MVP-committed FR-API-* item not yet built (`FR-API-1..4`
are all listed as MVP-required in §2's requirements table).

Deliberately RLS-exempt, the identical invitations pattern (see that
table's own migration comment): key *verification* — the read path that
turns a presented raw key into a principal — happens before any tenant
context can exist (the caller presents only a bearer string, not a
workspace id the server should trust yet), so it's looked up globally by
``prefix`` and the workspace match is enforced as an explicit typed
parameter in application code, not RLS. Admin-facing create/list/revoke
already know ``workspace_id`` from the URL and enforce it the same
explicit way invitations' own create/revoke do.

Also backfills the FK on ``audit_events.actor_key_id`` — a bare nullable
UUID with no FK since the tenancy-CRUD migration, whose own comment says
exactly this: "``api_keys`` doesn't exist until a later sprint."
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d5ca40686afd"
down_revision: str | None = "b5f9a5f82856"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_keys (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            prefix TEXT NOT NULL UNIQUE,
            secret_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            scopes TEXT[] NOT NULL,
            created_by UUID NOT NULL REFERENCES users(id),
            expires_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX api_keys_workspace_id_idx ON api_keys (workspace_id)")

    # No RLS (see module docstring) — the same posture as invitations,
    # for the same chicken-and-egg reason (verification has no tenant
    # context yet). GRANT is the whole access-control surface for this
    # table: app_api can read/write, nothing else.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys TO app_api")

    # audit_events.actor_key_id has waited for exactly this table.
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT audit_events_actor_key_id_fkey "
        "FOREIGN KEY (actor_key_id) REFERENCES api_keys(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_actor_key_id_fkey")
    op.execute("DROP TABLE IF EXISTS api_keys")
