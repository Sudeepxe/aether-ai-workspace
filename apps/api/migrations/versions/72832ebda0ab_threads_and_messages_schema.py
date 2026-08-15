"""threads and messages schema

Revision ID: 72832ebda0ab
Revises: f2c5391397e8
Create Date: 2026-08-15 21:23:38.100305

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Sprint 3 slice of the §8.1 schema catalog: ``threads`` and ``messages``
(ADR-8.2 — messages stored as rows with a transactional per-thread seq,
resolving OQ-3.4). Both carry ``workspace_id`` and get the identical
forced-RLS pattern established on ``memberships`` (Sprint 1) and used
again for ``invitations``' admin-facing sibling tables in Sprint 2 —
always accessed within an established tenant context, unlike the
RLS-exempt hash-lookup tables (refresh_tokens, invitations).

``thread_seq_counters`` is the explicit per-thread counter row ADR-8.2
requires: a naive ``max(seq)+1`` is a race under concurrent inserts to
the same thread, so seq assignment locks this row (``SELECT ... FOR
UPDATE``) within the same transaction as the message INSERT — one row
per thread, so lock contention is scoped to that thread only, never
cross-thread.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "72832ebda0ab"
down_revision: str | None = "f2c5391397e8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # --- threads (§8.1) -----------------------------------------------------
    op.execute(
        """
        CREATE TABLE threads (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            created_by UUID NOT NULL REFERENCES users(id),
            title TEXT,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
        """
    )
    # Covering index for list views (§8.1 hot-path indexes): cursor
    # pagination on (created_at, id) per ADR-4.4.
    op.execute(
        "CREATE INDEX threads_workspace_created_idx ON threads (workspace_id, created_at DESC, id)"
    )

    # --- thread_seq_counters (ADR-8.2's explicit locked counter row) -------
    op.execute(
        """
        CREATE TABLE thread_seq_counters (
            thread_id UUID PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
            next_seq BIGINT NOT NULL DEFAULT 1
        )
        """
    )

    # --- messages (§8.1, ADR-8.2) --------------------------------------------
    op.execute("CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system')")
    # 'error' is a done-EVENT status (§4.4), never a persisted message
    # status: an error before any token flowed persists no message row at
    # all, and an error mid-stream persists whatever content already
    # accumulated as 'partial' (§3.2.3's own failure-scenario wording) —
    # so the message table's status enum only ever needs these three.
    op.execute("CREATE TYPE message_status AS ENUM ('complete', 'partial', 'cancelled')")
    op.execute(
        """
        CREATE TABLE messages (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            thread_id UUID NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            seq BIGINT NOT NULL,
            role message_role NOT NULL,
            content TEXT NOT NULL,
            status message_status NOT NULL,
            client_message_id TEXT,
            model TEXT,
            prompt_tokens INT,
            completion_tokens INT,
            cost_microcents BIGINT,
            grounded BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (thread_id, seq),
            UNIQUE (thread_id, client_message_id)
        )
        """
    )
    # The single hottest index (§8.1): history pagination, seq DESC since
    # that's ADR-8.2's natural, gapless, clock-independent cursor key.
    op.execute("CREATE INDEX messages_thread_seq_idx ON messages (thread_id, seq DESC)")

    # --- row-level security (ADR-8.1 pattern, matching memberships) --------
    op.execute("ALTER TABLE threads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE threads FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON threads
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON messages
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # thread_seq_counters carries no workspace_id of its own (it's a pure
    # counter keyed by thread_id, one row per thread with no other tenant-
    # identifying data) — RLS on it would need a join to threads to mean
    # anything, and the only access path to it is already gated by the
    # same tenant-scoped connection that inserts into messages/threads in
    # the same transaction, so no policy is added here; it carries no
    # readable tenant data to leak.

    # --- grants (least privilege per role, ADR-8.1) ------------------------
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON threads, messages TO app_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON thread_seq_counters TO app_api")
    # app_worker: no grants yet — no worker consumer touches chat data
    # until memory compaction (S8) or metering rollups (S4) need it.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TYPE IF EXISTS message_status")
    op.execute("DROP TYPE IF EXISTS message_role")
    op.execute("DROP TABLE IF EXISTS thread_seq_counters")
    op.execute("DROP TABLE IF EXISTS threads")
