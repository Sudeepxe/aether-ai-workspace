"""outbox trace context

Revision ID: 8053de1b0539
Revises: 2cf76e54c64e
Create Date: 2026-08-25 00:23:44.581495

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

S9 (NFR-O-1, §3.8): a nullable-by-default JSONB column carrying the
producer's captured W3C ``traceparent`` (via
``opentelemetry.propagate.inject``) at enqueue time, so a worker
dispatching this row later can resume the same trace ("one trace per
user action across the async seam"). Deliberately its own column, not
folded into the existing ``payload`` column — ``payload`` is
domain-meaningful data a handler's business logic reads; trace context
is observability metadata with a different owner and lifecycle, and
mixing the two would leak an infrastructure concern into every
producer's domain-shaped payload construction. ``NOT NULL DEFAULT '{}'``
so every existing/future row has a well-defined (if sometimes empty —
tracing was unconfigured, or the row predates this migration) value; the
worker-side ``linked_span`` helper already treats an empty carrier as
"start an uncorrelated span" rather than an error.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8053de1b0539"
down_revision: str | None = "2cf76e54c64e"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE outbox ADD COLUMN trace_context JSONB NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE outbox DROP COLUMN IF EXISTS trace_context")
