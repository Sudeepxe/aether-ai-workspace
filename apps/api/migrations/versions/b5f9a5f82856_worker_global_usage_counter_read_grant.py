"""worker global usage counter read grant

Revision ID: b5f9a5f82856
Revises: 8053de1b0539
Create Date: 2026-08-25 13:31:45.360224

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

S9 (§10.4's Cost dashboard): the worker's poll loop reads
``global_usage_counter.settled_microcents`` each cycle to publish the
``aether_global_spend_microcents`` gauge — a genuinely new read need,
not previously granted (only ``app_api`` could read this row, for the
global-kill-switch admission check on the request path). Narrow,
additive SELECT-only grant, same least-privilege posture as every other
worker-plane read added across S8/S9 (invitations/memory_summaries in
issue #86, outbox stats in issue #94).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b5f9a5f82856"
down_revision: str | None = "8053de1b0539"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON global_usage_counter TO app_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON global_usage_counter FROM app_worker")
