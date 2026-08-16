"""chunk content hash dedupe index

Revision ID: 92e837a3a68f
Revises: 20323f010125
Create Date: 2026-08-16 17:57:35.078213

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Issue #47's content-hash embedding cache (§3.2.7's cost note: dedupe
makes re-uploads/duplicate chunks near-free) looks up existing embedded
chunks by ``(workspace_id, content_sha256)`` before calling the
embedding provider. Partial on ``embedding IS NOT NULL`` since only
already-embedded rows are useful cache hits — chunks mid-pipeline
(embedding still null) would never match and would only bloat the
index.

Also grants app_worker INSERT on outbox: until now only app_api ever
wrote an outbox row (password resets, invitations — API-plane actions).
Issue #47's ``document.ready``/``document.failed`` events are the first
worker-plane outbox producer (§8's "chunk-batch upsert + document
status + outbox" invariant) — app_worker previously had SELECT/UPDATE
only (the dispatcher's read/mark-dispatched side), no INSERT.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "92e837a3a68f"
down_revision: str | None = "20323f010125"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX chunks_content_hash_idx ON chunks (workspace_id, content_sha256) "
        "WHERE embedding IS NOT NULL"
    )
    op.execute("GRANT INSERT ON outbox TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON outbox FROM app_worker")
    op.execute("DROP INDEX IF EXISTS chunks_content_hash_idx")
