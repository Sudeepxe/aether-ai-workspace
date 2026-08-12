# Chapter 8: Database Design

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 8.0 Decision D8-1: Tenancy Model — Shared Tables + RLS

**Chosen: shared tables, `tenant_id` (= workspace_id) column on every tenant-scoped table, Postgres Row-Level Security as the enforcement backstop** (per §3.7.2), three runtime roles (`app_api`, `app_worker`, `app_migrator`) with distinct grants.

- **Alternatives:** **schema-per-tenant** — real isolation, but 10K schemas (the 100× envelope) makes migrations an O(tenants) operation, connection pools fragment, and pg_dump/catalog bloat is documented pain; rejected. **Database-per-tenant** — the isolation gold standard and correct for regulated single-tenant enterprise deals; operationally absurd for a self-serve platform at this team size; recorded as the eventual "enterprise dedicated" tier answer. **Citus/sharding extensions** — machinery for a scale problem the envelope doesn't have; the `tenant_id`-everywhere discipline is precisely what makes Citus adoptable *later* without rework (distribution key already in every table and every query).
- **RLS mechanics:** policies keyed on `current_setting('app.tenant_id')`, set per-transaction (`SET LOCAL`) by the repository layer from the request context; `app_migrator` alone may bypass (migrations/ops); **RLS enabled *and forced* even for table owners** — a superuser habit dying in review rather than production.

### 8.1 Schema Catalog

Described structurally (no DDL — implementation code comes later). Conventions: PK `id UUIDv7`; `created_at`/`updated_at` RFC 3339 server-set; tenant-scoped tables carry `workspace_id` FK + RLS; soft-deletable tables carry `deleted_at`.

**Identity & tenancy (RLS-exempt or self-scoped):**

| Table | Key columns (beyond conventions) | Notes |
|---|---|---|
| `users` | email (citext, unique), password_hash?, display_name, status | Password null for OAuth-only users |
| `identities` | user_id FK, provider, provider_subject (unique per provider), email_verified | OAuth/OIDC link table — SSO-ready (§7.2) |
| `workspaces` | name, slug, settings jsonb, model_policy jsonb, deleted_at | |
| `memberships` | workspace_id, user_id, role enum, unique(workspace_id,user_id) | The tenancy join; role per §7.3 |
| `invitations` | workspace_id, email, role, token_hash, expires_at, consumed_at, invited_by | Single-use (consumed_at check) |
| `refresh_tokens` | user_id, family_id, token_hash, device_fingerprint, used_at, successor_id?, expires_at, revoked_at | Family model + grace idempotency (§7.1) |
| `api_keys` | workspace_id, prefix, secret_sha256, scopes[], expires_at?, last_used_at (coarse), revoked_at | §7.4 |

**Conversation plane (all tenant-scoped):**

| Table | Key columns | Notes |
|---|---|---|
| `threads` | workspace_id, created_by, title, settings jsonb, deleted_at | Cursor-listed by (created_at,id) |
| `messages` | workspace_id, thread_id, **seq bigint**, role enum, content text, status enum(complete\|partial\|cancelled), client_message_id (unique per thread), model, prompt_tokens, completion_tokens, cost_microcents, grounded bool | **D8-2 below**; unique(thread_id, seq) |
| `message_citations` | message_id, chunk_id, ordinal, score | Provenance join — survives even if chunk later deleted (see F-2) |
| `feedback` | message_id, user_id, rating, reason?, unique(message_id,user_id) | FR-CH-6 → eval curation |
| `memory_summaries` | thread_id, upto_seq, content, model, token_count | Rolling compaction (§3.2.6); latest-wins per thread |

**Knowledge plane (tenant-scoped):**

| Table | Key columns | Notes |
|---|---|---|
| `documents` | workspace_id, filename, content_sha256, mime, size_bytes, object_key, status enum(queued…ready\|failed), failure_stage?, failure_reason?, version int, superseded_by?, deleted_at | Status = event projection (FR-KB-2); versioning for FR-KB-6 |
| `chunks` | workspace_id, document_id, section_path, page_range, char_span, content text, content_sha256, token_count, **embedding vector(1536)**, **embedding_model, embedding_version**, **content_tsv (generated column)** | The heart of RAG; embedding-version per Ch. 3 F-5; tsvector as *generated* column (see F-3) |

**Governance & operations plane:**

| Table | Key columns | Notes |
|---|---|---|
| `usage_events` | workspace_id, user_id?, kind enum(chat\|embed\|rewrite\|compact), model, prompt_tokens, completion_tokens, cost_microcents, generation_id?, occurred_at | Append-only ledger; **monthly range partitions (D8-3)** |
| `budgets` | workspace_id (PK), monthly_limit_microcents, soft_pct, current_period_start, settled_microcents, updated_at | Settled counter maintained by metering consumer; ETag = updated_at |
| `audit_events` | workspace_id?, actor_user_id?, actor_key_id?, action, target_type, target_id, metadata jsonb (PII-minimized per Ch. 7 F-3), occurred_at | **INSERT-only grants; monthly partitions**; workspace null for system-level events |
| `outbox` | id, aggregate_type, aggregate_id, event_type, tenant_id, payload jsonb, trace_context, created_at, dispatched_at? | §3.2.9; index on (dispatched_at) partial WHERE null |
| `idempotency_keys` | workspace_id, key, request_sha256, response_snapshot jsonb, status_code, expires_at, unique(workspace_id,key) | 24 h TTL, reaped by housekeeping (Ch. 4 ADR-4.6) |
| `export_jobs` / `deletion_jobs` | workspace_id, kind, status, evidence jsonb, requested_by, completed_at | FR-AD-5 / DF-3 saga state + deletion evidence |

~20 tables. Deliberately absent: no `sessions` table (JWT-stateless + Redis denylist); no separate `generations` table (generation state is ephemeral — Redis buffer + terminal state folded into `messages.status`); no ORM-style polymorphic tables (join tables are explicit).

### 8.2 Decision D8-2: Message Storage (resolves OQ-3.4)

**Chosen: relational rows with a per-thread monotonic `seq`**, assigned transactionally at insert (per-thread counter), unique `(thread_id, seq)`.

- **Why:** `seq` gives gapless, clock-independent ordering (client clocks and even server timestamps can tie or skew — Ch. 4 conventions already distrust clocks), a natural cursor for pagination, the join key for `Last-Event-ID` reconciliation (§4.4: event id = generation:seq), and the anchor for `memory_summaries.upto_seq`.
- **Alternatives:** **timestamp ordering** — ties under concurrency, skew under clock drift; rejected as sole key (kept as display metadata). **JSONB message arrays per thread** — one row per thread sounds cheap until: row rewrite amplification per message, no per-message FKs (citations, feedback), no partial indexing, lock contention on hot threads; rejected. **Append-only event log (messages as events, threads as projections)** — architecturally elegant, aligns with the outbox; rejected as *primary* store: every read becomes a projection problem, and the product's read patterns (paginated history) are exactly what relational rows serve natively. Event-sourcing the whole conversation plane is complexity without a requirement demanding it (per the mandate against over-engineering).

### 8.3 Indexing & Performance

- **Hot-path indexes:** `messages(thread_id, seq desc)` — the single hottest index (history pagination); `threads(workspace_id, created_at desc, id)` covering for list views; `chunks` HNSW on embedding (m=16, ef_construction=64 initial; `ef_search` runtime-tuned against the p95 ≤ 400 ms budget) + GIN on `content_tsv` (the hybrid lexical leg) — **both indexes must include/filter `workspace_id` + active `embedding_version`** (partial index per version during migrations); `usage_events(workspace_id, occurred_at)` per-partition; `outbox(dispatched_at) WHERE dispatched_at IS NULL` partial (dispatcher scans only pending); `refresh_tokens(family_id)`, `(token_hash)`; `documents(workspace_id, status)` partial on non-terminal statuses (the "what's processing" view).
- **Row-width reality check (the pgvector cost nobody prices):** a chunk row ≈ 6 KB of vector + ~2 KB text ⇒ 1M chunks ≈ 8–10 GB with index overhead; HNSW index memory-resident working set is the true constraint on the 8 GB VPS → **`halfvec(1536)`** (2-byte floats — pgvector ≥ 0.7) halves vector storage/memory with negligible measured recall loss in published benchmarks; adopted as the **default, verified by the eval harness before commit** (recall@k vs. float32 on the golden corpus) — D8-4.
- **Capacity forecast at envelope:** messages ~10 GB/yr worst case, usage/audit partitions ~1 GB/mo pruned by retention, chunks per above — single-node comfortable; the first real pressure point is HNSW memory, which is exactly the D3-2 escape-hatch trigger, consistently.

### 8.4 Partitioning, Migrations, Lifecycle — Decision D8-3

- **Partitioned:** `usage_events`, `audit_events` — monthly range partitions from day one (append-only, time-queried, retention-pruned by partition drop — deleting a month is O(1), not a tombstone storm). **Not partitioned:** `messages`, `chunks` at v1 (envelope doesn't justify it; partition keys and the migration path documented — messages by thread-hash only in the 100× story).
- **Migrations:** expand-contract exclusively (NFR-A-2): additive change → dual-write/backfill → cutover → contraction release later; N−1 code always runs against N schema (rollback = redeploy previous image, never a down-migration in prod — down-migrations exist for dev only). CI gate: migration applied against a production-shaped dataset snapshot (synthetic) + N−1 compatibility test suite.
- **Data lifecycle:** soft-delete (`deleted_at`) for user-facing undo windows → hard-delete via `deletion_jobs` sagas (DF-3) with per-store evidence; retention: audit 12 mo (partition-dropped), usage 24 mo rolled up to monthly aggregates after 3 mo, idempotency 24 h, refresh-token rows purged 30 d post-expiry. User hard-delete: `users` row anonymized (tombstone), FKs preserved, audit joins resolve to "deleted user" (Ch. 7 F-3 obligation discharged).

### 8.5 Transactions, Consistency, Failure & DR (DB plane)

- **Invariant transactions (each a single ACID unit):** message insert + seq allocation + outbox; chunk-batch upsert + document status + outbox; document delete: chunks+vectors+citations handling (F-2) + status + outbox — the pgvector dividend (ADR-3.2) in action; refresh rotation (mark-used + insert-successor — the race absorbed by the same-successor idempotency, §7.1).
- **Isolation:** default `READ COMMITTED` with explicit row locks where invariants demand (seq counters, budget settlement `SELECT … FOR UPDATE`); `SERIALIZABLE` rejected globally (retry-storm coupling on hot rows), used nowhere — every invariant above is lock-provable at `READ COMMITTED`.
- **Failure modes:** connection exhaustion → PgBouncer + pool math (§4.5) + pool-wait alerting; long-running ingestion tx blocking autovacuum → chunk batches capped (500/tx); replication lag (Phase 3 replicas) → retrieval reads tolerate seconds of staleness by design, auth/budget reads pinned to primary; bloat on `chunks` churn → autovacuum tuned per-table, `pg_repack` in the runbook.
- **DR:** inherits §3.9.4 (PITR ≤ 1 h RPO); restore drill includes RLS verification (restored DB must *still* enforce isolation before serving — a restore that silently drops policies is the nightmare scenario, so the drill asserts it) and HNSW rebuild timing against the RTO.

### 8.6 ADRs, Interview Q&A, Mistakes, Roadmap, Checklist

| ADR | Decision | Revisit trigger |
|---|---|---|
| ADR-8.1 | Shared tables + forced RLS + three-role grants (D8-1) | Dedicated-tenant enterprise tier |
| ADR-8.2 | Messages as rows with transactional per-thread `seq` (D8-2, closes OQ-3.4) | — |
| ADR-8.3 | Monthly range partitions for usage/audit only; retention by partition drop (D8-3) | messages > ~100M rows |
| ADR-8.4 | `halfvec` vectors pending recall verification on golden set | Recall delta > 1 pt ⇒ revert to float32 |
| ADR-8.5 | No prod down-migrations; expand-contract + N−1 CI gate | — |
| ADR-8.6 | Citations denormalize provenance snapshot (see F-2) | — |

**Interview Q&A.** *Q1: "Why RLS when your app layer already filters by tenant?"* — Ideal: defense in depth with independent failure modes — app filters fail by *bug*, RLS fails by *misconfiguration*; both failing simultaneously is the design target; plus RLS covers ad-hoc/ops queries no app code mediates; names the cost (per-query setting, planner constraints) as the price paid. *Q2: "Message ordering — why not timestamps?"* — Ideal: ties, skew, and retry-duplication make time a display property, not an ordering key; per-thread seq is gapless, cursor-able, and joins the streaming resume story end-to-end. *Q3: "What breaks first in your DB at 100× and how do you know?"* — Ideal: HNSW memory (not disk, not writes) — cites the row-width arithmetic, the halfvec mitigation, and that the same threshold is the pre-committed D3-2 extraction trigger — three chapters telling one consistent story. *Q4: "A user deletes a document that's cited in a thread. What happens?"* — Ideal: walks F-2's resolution (below): citations keep a provenance *snapshot*, the chunk row goes away, the UI renders "source removed" — deletion honored, history intact, no dangling FK, no silent nulls. *Q5: "Why are down-migrations banned in prod?"* — Ideal: rollback is *code* rollback against an N−1-compatible schema; a down-migration under incident pressure is an untested destructive script pointed at your only data — expand-contract makes backward compatibility a property, not a hope.

**Common mistakes.** Tenant filtering in app code only; UUIDs stored as text (16-byte type exists — 2–3× index bloat otherwise); no partitioning plan for append-forever tables until the first 2 TB `DELETE`; ordering by `created_at` and shipping the tie bug; FK cascades from user deletion nuking audit history (tombstone instead); indexing every column "to be safe" (write amplification, planner confusion); JSONB as schema-avoidance until the first query needs an index on a nested key; ignoring vector row width until the index no longer fits in RAM; testing migrations only against empty databases.

**Roadmap.** Phase 2: `collections` table + retrieval filters (FR-KB-7), document version supersedence flows (FR-KB-6), agent-run + trace tables (additive, shaped by §6.6's frozen interfaces). Phase 3: read replicas with staleness-tiered routing; doc-level ACL columns + retrieval-time filter (OQ-2.4 default confirmed); per-tenant encryption envelope. 100×: messages partitioning, Citus evaluation (distribution key pre-positioned), vector extraction per D3-2.

**Checklist:** every table has an owner-chapter tracing its access pattern ✓; every FK has a defined deletion behavior (cascade/restrict/tombstone — audited in F-2's sweep) ✓; RLS forced incl. owners, verified in restore drill ✓; append-only tables INSERT-only by grant ✓; retention defined per table ✓; embedding-version machinery present (Ch. 3 F-5 discharged) ✓; audit PII-minimization present (Ch. 7 F-3 discharged) ✓.

### 8.7 Self-Review Record — Chapter 8

| Finding | Severity | Resolution |
|---|---|---|
| F-1: Per-thread `seq` via naive `max(seq)+1` is a race under concurrent inserts (two turns, same seq, unique-violation retries) | Medium | Explicit per-thread counter row locked `FOR UPDATE` within the insert tx (contention scope = one thread = acceptable by definition; a thread is a serial conversation) — documented as the deliberate serialization point |
| F-2: **Deletion vs. citations contradiction** — DF-3 hard-deletes chunks, but `message_citations` FK'd to chunks: cascade would silently rewrite conversation history (citations vanishing from old answers); restrict would block deletion (violates FR-KB-5) | **High** | Citations denormalize a provenance snapshot (doc title, section_path, page) at write time; chunk FK becomes nullable-on-delete; UI renders "source removed" for tombstoned citations. Deletion completes fully; history stays honest (ADR-8.6) |
| F-3: tsvector maintained by trigger in draft — trigger-maintained derived columns are the classic drift/omission bug | Low | `content_tsv` as a **generated column** (declarative, unmissable); write amplification accepted as the cost of correctness |
| F-4: `budgets.settled_microcents` updated per usage event = hot-row contention under burst | Medium | Metering consumer batches settlements (per-workspace accumulate, flush ≤ 5 s — within NFR-O-2); admission check reads settled + in-flight estimate, consistent with §3.2.14 |
| F-5: Vector row width priced only for float32; the 8 GB VPS HNSW working set was borderline at envelope | Medium | `halfvec` default with eval-verified recall gate (ADR-8.4) — capacity margin restored 2× |

**Verdict:** pass. F-2 is the chapter's justification in miniature: two sacred requirements (provable deletion, honest history) were in silent contradiction that only a schema-level FK analysis exposes — resolved by denormalizing exactly the fields provenance requires and nothing more. The FK-deletion-behavior sweep it triggered is now a checklist item for every future table.

---

