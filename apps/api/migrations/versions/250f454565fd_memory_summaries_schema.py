"""memory summaries schema

Revision ID: 250f454565fd
Revises: 472446d431a3
Create Date: 2026-08-23 21:45:36.111296

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "250f454565fd"
down_revision: str | None = "472446d431a3"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_summaries (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            thread_id UUID NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            upto_seq BIGINT NOT NULL,
            content TEXT NOT NULL,
            model TEXT NOT NULL,
            token_count INT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (thread_id)
        )
        """
    )
    # "latest-wins per thread" (§8.1) — one row per thread, upserted as
    # compaction advances upto_seq; the unique(thread_id) constraint above
    # is what makes the upsert's ON CONFLICT (thread_id) target valid.
    op.execute("ALTER TABLE memory_summaries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_summaries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON memory_summaries
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    # app_api only: compaction is driven by the same pool-bound path
    # SendMessage already uses for message persistence (issue #60's
    # MessageStorePort precedent), not a separate worker-plane role.
    op.execute("GRANT SELECT, INSERT, UPDATE ON memory_summaries TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_summaries")
