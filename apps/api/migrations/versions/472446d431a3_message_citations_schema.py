"""message citations schema

Revision ID: 472446d431a3
Revises: 92e837a3a68f
Create Date: 2026-08-17 01:14:52.453351

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Sprint 6 slice of the §8.1 schema catalog (ADR-8.6, FR-KB-3, FR-CH-3):

``message_citations`` denormalizes a provenance snapshot (document
title, section_path, page range) at write time rather than relying on
a live join to ``chunks`` — resolving the direct conflict between
FR-KB-3 (cite source documents with chunk-level provenance) and
FR-KB-5 (documents/chunks must be provably, fully deletable). The
``chunk_id`` FK is deliberately **ON DELETE SET NULL**, not CASCADE
(which would silently rewrite historical answers when a document is
later deleted) and not RESTRICT (which would block FR-KB-5's provable
deletion) — the citation row survives with its snapshot fields intact,
and the UI (issue #61) renders a "source removed" state for a null
``chunk_id``.

This table also satisfies FR-CH-3's "(if RAG) retrieved chunk IDs"
storage requirement — in this MVP, the chunks retrieved into an
answer's context ARE its citations (no real LLM is available in this
environment to selectively narrow to a genuinely-cited-only subset).

Write-once from the API tier only (app_api persists a message and its
citations together, issue #60) — no UPDATE/DELETE grant needed:
citations are never edited, and removal happens only as a side effect
of the referenced chunk's own deletion (the FK's ON DELETE SET NULL),
never a direct citations-table delete. app_worker never touches this
table at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "472446d431a3"
down_revision: str | None = "92e837a3a68f"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE message_citations (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
            document_title TEXT NOT NULL,
            section_path TEXT NOT NULL,
            page_start INT,
            page_end INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (page_end IS NULL OR page_start IS NULL OR page_end >= page_start)
        )
        """
    )
    # The read pattern (§8.1): every citation for one message, fetched
    # alongside that message's content.
    op.execute("CREATE INDEX message_citations_message_idx ON message_citations (message_id)")

    op.execute("ALTER TABLE message_citations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE message_citations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON message_citations
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # app_api persists a message and its citations together (issue #60)
    # and reads them back for history display — no UPDATE/DELETE, see
    # this migration's own docstring for why. app_worker: no grant at
    # all, matching messages/threads' own "no worker consumer touches
    # chat data" posture.
    op.execute("GRANT SELECT, INSERT ON message_citations TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS message_citations")
