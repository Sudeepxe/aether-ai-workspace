"""outbox and password reset tokens

Revision ID: f2c5391397e8
Revises: 9b9e2d39ed9a
Create Date: 2026-08-15 20:30:56.380630

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

- ``outbox`` — transactional outbox (§3.2.9, §8.1), the mechanism behind
  ADR-11.1's "all sends go via the worker, queue-backed and retried":
  the app writes an outbox row in the same transaction as the business
  mutation it accompanies (e.g. an invitation and its
  ``invitation.email`` outbox row commit or roll back together), and the
  worker polls for undispatched rows and dispatches them. Deliberately
  no RLS: ``app_api`` only ever inserts its own already-correctly-scoped
  rows (tenant_id set by the producer, not enforced by a policy here),
  and the worker's whole job is dispatching *across* tenants, which RLS
  would actively work against. Retry is a bounded ``attempts`` counter
  (§3.6.2's "capped attempts (5, exp backoff) [->] DLQ") rather than a
  separate dead-letter table for now — a stuck row (attempts >= 5,
  dispatched_at still null) is the DLQ signal until that table exists;
  tracked as a follow-up, not silently dropped.
- ``password_reset_tokens`` — ADR-11.1's single-use, hashed, 30-minute
  password-reset token. Not in the original §8.1 catalog (written before
  ADR-11.1's gap-remediation pass added the whole flow); added here
  following the identical shape already established for refresh_tokens
  and invitations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f2c5391397e8"
down_revision: str | None = "9b9e2d39ed9a"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE outbox (
            id UUID PRIMARY KEY,
            aggregate_type TEXT NOT NULL,
            aggregate_id UUID NOT NULL,
            event_type TEXT NOT NULL,
            tenant_id UUID,
            payload JSONB NOT NULL,
            attempts INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            dispatched_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX outbox_pending_idx ON outbox (created_at) WHERE dispatched_at IS NULL")
    op.execute("GRANT INSERT ON outbox TO app_api")
    op.execute("GRANT SELECT, UPDATE ON outbox TO app_worker")

    op.execute(
        """
        CREATE TABLE password_reset_tokens (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX password_reset_tokens_user_id_idx ON password_reset_tokens (user_id)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON password_reset_tokens TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS password_reset_tokens")
    op.execute("DROP TABLE IF EXISTS outbox")
