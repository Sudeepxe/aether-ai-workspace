# Runbook: Disaster recovery — restore drill

- **Verifying automation:**
  [`infra` → `apps/api/scripts/verify_restore_drill.py`](../../apps/api/scripts/verify_restore_drill.py)
  (`restore-drill.yml`, quarterly + `workflow_dispatch`) proves this
  procedure for real: two independent Postgres instances, a real
  `pg_dump`/`pg_restore` round trip, a real post-restore RLS assertion,
  a real vector-index rebuild, a real app request against the restored
  instance.

## When to run this

- Scheduled (quarterly, automated).
- After any Postgres primary loss (§3.9.4).
- Before trusting a backup you've never actually restored — "Schrödinger's
  backup" (untested backups that turn out unusable) is explicitly named
  as a common DR mistake (§10.5) this drill exists to rule out.

## RPO / RTO targets (§3.9.4)

| Store | Strategy | RPO | RTO |
|---|---|---|---|
| Postgres | WAL archiving (PITR) to object storage | ≤ 1 h | ≤ 4 h |
| Object storage | Versioning + (cloud) cross-region replication | ~0 | minutes |
| Vectors | **Not backed up — derived data** (ADR-2.3): rebuilt by re-embedding from `chunks.content` | n/a | ≤ 4 h (rebuild cost pre-computed: ~1M chunks ≈ 2–4 h, ~$100 embedding spend at real-provider prices) |
| Redis | Not backed up — every role rebuildable (outbox replay, cache warm, limits reset conservative) | ~0 impact | — |

## Procedure

The literal DR playbook's first line (§3.9.4's interview answer): *"new
VPS from infra scripts → restore drill procedure → vectors rebuild
within budget → DNS cutover."* This runbook covers the restore-drill
core (steps 1–5 below); VPS provisioning and DNS cutover are S11's
real-VPS gap — no real server exists in this environment to exercise
them against (see [`docs/architecture/prr.md`](../architecture/prr.md)
for the full honest scope note).

1. **Provision a fresh Postgres instance and run migrations against it**
   (`alembic upgrade head`) — this recreates the current schema, roles,
   and grants from source, not from the backup. A real recovery
   deploys the *current* code's migrations first; the backup restores
   *data* into that already-correct schema, never the other way
   around (this is also why the drill excludes `alembic_version` from
   the data restore — the fresh migration run is what's authoritative
   for schema state, not a stale snapshot of it).
2. **Restore the WAL/backup archive's data** into the freshly-migrated
   instance (`pg_restore --data-only` against a `pg_dump --data-only`
   backup, in production terms: a PITR restore to the target recovery
   point).
3. **Verify RLS survived** — the literal §8.5 "nightmare scenario"
   check: query cross-tenant data against the restored instance and
   confirm it's still denied. A restore that silently drops RLS
   policies must never be trusted, even if the data itself looks
   correct.
4. **Rebuild vectors** — vectors are derived data (ADR-2.3), not part
   of the backup. Re-embed affected chunks (the real ingestion
   pipeline's embedding adapter, same code path as normal ingestion)
   and confirm the HNSW index is populated again. Time this step —
   it's the dominant cost at real data volumes (the ~2–4h/~$100
   estimate above), not the SQL restore itself.
5. **Real e2e-lite check** — point the actual app at the restored
   instance and prove it serves real requests (register, log in, read
   real restored data). Rows existing in a table is necessary but not
   sufficient; the app has to actually work against them.
6. **DNS cutover** (real VPS only — not exercised by the automated
   drill in this repo, since no real VPS/DNS exists in this
   environment; see the PRR document's honest gap note).

## Honest scale note

The automated drill's dataset is a handful of rows, not a
production-scale tenant. It proves the *mechanism* — backup, restore,
RLS survival, vector rebuild, a real app serving restored data — works
correctly, not that it meets the RTO budget at production data
volumes. This environment has no real production data to measure
that against; the ~2–4h vector-rebuild estimate above is the
blueprint's own pre-computed arithmetic (§3.9.4), not something this
repo has independently re-derived at scale.

## Verification (how you know it's fixed)

`make devon-quickstart`-style: run `apps/api/scripts/verify_restore_drill.py`
directly (needs `docker`, `uv`, and `pg_dump`/`pg_restore` on `PATH`) or
trigger `restore-drill.yml` via `workflow_dispatch` — a clean `PASS`
with the measured RTO under the 4h budget is the acceptance bar.
