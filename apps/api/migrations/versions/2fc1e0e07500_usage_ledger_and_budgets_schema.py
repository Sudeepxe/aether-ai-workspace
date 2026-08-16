"""usage ledger and budgets schema

Revision ID: 2fc1e0e07500
Revises: 72832ebda0ab
Create Date: 2026-08-16 00:05:00.000000

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Sprint 4 slice of the §8.1 schema catalog:

- ``usage_events`` — the append-only usage ledger (FR-AD-2/3), monthly
  range partitions from day one (ADR-8.3, identical pattern to
  ``audit_events``). ``workspace_id`` is NOT NULL here (unlike
  audit_events): every usage event is, by construction, attributed to a
  tenant-scoped chat turn or ingestion job — there is no system/auth-plane
  usage event — so the plain-equality RLS policy (memberships' pattern)
  applies, not audit_events' NULL-safe variant.
- ``budgets`` — one row per workspace, ``settled_microcents`` settled
  synchronously via app_api as part of the request that generated the
  usage (see adapters/postgres/usage_ledger.py's module docstring for
  why per-event settlement is still correct under concurrent load, just
  not throughput-optimized). app_worker's grants below are provisioned
  ahead of need for §8's F-4 self-review finding (a later batched-
  settlement consumer) so landing it doesn't require a follow-up
  migration — it performs no settlement yet. ETag = updated_at, matching
  workspaces' own PATCH concurrency pattern.
- ``global_usage_counter`` — a single, non-tenant-scoped row tracking
  total settled spend across every workspace, for the global monthly
  kill switch (NFR-C-1). Deliberately NOT RLS-scoped: it isn't tenant
  data, and ``budgets`` (which is forced-RLS, tenant-scoped) cannot
  answer a cross-tenant SUM under app_api's per-request tenant context —
  a real gap caught by this sprint's own integration tests. A dedicated
  counter row, updated atomically in the same transaction as each
  per-workspace settlement, avoids touching budgets' RLS policy at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2fc1e0e07500"
down_revision: str | None = "72832ebda0ab"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # --- usage_events (FR-AD-2/3, ADR-8.3) ----------------------------------
    op.execute("CREATE TYPE usage_event_kind AS ENUM ('chat', 'embed', 'rewrite', 'compact')")
    op.execute(
        """
        CREATE TABLE usage_events (
            id UUID NOT NULL,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id),
            kind usage_event_kind NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INT NOT NULL,
            completion_tokens INT NOT NULL,
            cost_microcents BIGINT NOT NULL,
            generation_id UUID,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
        """
    )
    op.execute(
        "CREATE INDEX usage_events_workspace_occurred_idx ON usage_events (workspace_id, occurred_at)"
    )

    # Current + next month only — matching audit_events' documented,
    # tracked-not-automated posture for ongoing partition provisioning.
    op.execute(
        """
        CREATE TABLE usage_events_2026_08 PARTITION OF usage_events
            FOR VALUES FROM ('2026-08-01') TO ('2026-09-01')
        """
    )
    op.execute(
        """
        CREATE TABLE usage_events_2026_09 PARTITION OF usage_events
            FOR VALUES FROM ('2026-09-01') TO ('2026-10-01')
        """
    )

    op.execute("ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE usage_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON usage_events
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # INSERT-only by grant, same tamper-resistance posture as audit_events
    # (§3.7.3) — a usage ledger app code can rewrite is not a ledger.
    op.execute("GRANT SELECT, INSERT ON usage_events TO app_api")
    # app_worker doesn't consume usage_events yet — this grant is
    # provisioned ahead of need for a future reconciliation/backfill job
    # (§3.2.14), so landing one later needs no follow-up migration.
    op.execute("GRANT SELECT, INSERT ON usage_events TO app_worker")

    # --- budgets (§3.2.14, FR-AD-3) ------------------------------------------
    op.execute(
        """
        CREATE TABLE budgets (
            workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
            monthly_limit_microcents BIGINT NOT NULL,
            soft_pct INT NOT NULL DEFAULT 80,
            current_period_start DATE NOT NULL,
            settled_microcents BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (soft_pct > 0 AND soft_pct <= 100),
            CHECK (monthly_limit_microcents >= 0),
            CHECK (settled_microcents >= 0)
        )
        """
    )

    op.execute("ALTER TABLE budgets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE budgets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON budgets
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # app_api needs UPDATE too (unlike the ledger): PUT /budget (Admin,
    # ETag) updates monthly_limit_microcents directly, and settlement
    # (adapters/postgres/usage_ledger.py's record()) both go through
    # this role today.
    op.execute("GRANT SELECT, INSERT, UPDATE ON budgets TO app_api")
    # app_worker performs no settlement yet — see this file's module
    # docstring — but is granted UPDATE (never INSERT/DELETE: it would
    # never create or remove a workspace's budget row) ahead of a future
    # batched-settlement consumer (§8's F-4).
    op.execute("GRANT SELECT, UPDATE ON budgets TO app_worker")

    # --- global_usage_counter (NFR-C-1's global kill switch) -----------------
    op.execute(
        """
        CREATE TABLE global_usage_counter (
            id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
            settled_microcents BIGINT NOT NULL DEFAULT 0 CHECK (settled_microcents >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # The CHECK(id) + PRIMARY KEY combination caps this table at exactly
    # one row (the classic Postgres "singleton table" pattern) — seeded
    # here so check_global() can always assume the row exists.
    op.execute("INSERT INTO global_usage_counter (id, settled_microcents) VALUES (true, 0)")

    # Deliberately no RLS: this is a single global aggregate, not
    # per-tenant data — see the module docstring above.
    op.execute("GRANT SELECT, UPDATE ON global_usage_counter TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS global_usage_counter")
    op.execute("DROP TABLE IF EXISTS budgets")
    op.execute("DROP TABLE IF EXISTS usage_events")
    op.execute("DROP TYPE IF EXISTS usage_event_kind")
