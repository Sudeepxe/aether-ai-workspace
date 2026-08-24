"""feedback schema

Revision ID: 14494bfcbc14
Revises: 250f454565fd
Create Date: 2026-08-23 22:10:23.627903

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "14494bfcbc14"
down_revision: str | None = "250f454565fd"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE feedback (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id),
            rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (message_id, user_id)
        )
        """
    )
    # One feedback row per (message, user) — a caller changing their mind
    # upserts via ON CONFLICT (message_id, user_id), it doesn't duplicate
    # (§8.1, same "latest-wins" shape as memory_summaries' UNIQUE(thread_id)).
    op.execute("CREATE INDEX feedback_message_idx ON feedback (message_id)")
    op.execute("ALTER TABLE feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON feedback
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    # app_api only: feedback submission is a plain, transaction-scoped
    # WorkspaceScope mutation (like citations), not a worker-plane concern.
    op.execute("GRANT SELECT, INSERT, UPDATE ON feedback TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feedback")
