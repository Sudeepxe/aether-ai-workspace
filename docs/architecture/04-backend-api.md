# Chapter 4: Backend & API Design

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 4.0 Decision D4-1: Runtime & Framework (resolves OQ-3.1)

**The decision: Python 3.12+, FastAPI on uvicorn (asyncio), Pydantic v2 as the single validation/serialization contract.** One codebase produces both processes from D3-1 (`api`, `worker`).

1. **Why chosen.**
   - **Ecosystem gravity is the decisive argument, not language preference.** The worker *must* run Python regardless of API choice: document parsers (PyMuPDF, unstructured), tokenizers (tiktoken), embedding/eval tooling (the entire RAG-evaluation ecosystem) are Python-first. Choosing a different API language means two codebases, two CI matrices, duplicated domain models, and a serialization boundary through the middle of the hexagon — for one engineer, that is the worst possible trade.
   - **The workload is I/O-bound end-to-end.** A chat turn is: Redis call, PG queries, HTTPS to a provider, stream relay. asyncio handles thousands of concurrent SSE connections per process; the GIL constrains CPU-bound parallelism, which lives in worker processes (parsing, chunking) — process-parallel, GIL-irrelevant.
   - **FastAPI generates the OpenAPI 3.1 contract from the same Pydantic models that validate requests** — FR-API-3 (published, accurate spec) becomes a build artifact instead of a maintenance chore, and drift between docs and behavior becomes structurally impossible.

2. **Alternatives considered.**
   - **Go (chi/echo).** The strongest rival: better raw concurrency, single-binary deploys, lower memory. *Rejected because* it forces the two-codebase split described above (Go API + Python workers), and its AI-adjacent ecosystem (parsers, tokenizers, eval) is thin. Honest concession: at 100× scale with a team, the extracted LLM Router (D3-1 evolution ①) is a natural Go service.
   - **TypeScript (NestJS/Fastify).** Shared types with the React frontend is a real benefit. *Rejected because* the worker-side ecosystem problem is identical to Go's, NestJS adds a heavy abstraction layer over a thin need, and end-to-end type sharing is achievable anyway by generating TS types from the OpenAPI spec (Ch. 5).
   - **Rust (Axum).** Performance the envelope does not need, at a development-velocity cost the schedule cannot pay; hiring-signal value is real but this project's signal is architecture, not borrow-checking.
   - **Django/Flask (sync Python).** Rejected within-language: WSGI's thread-per-connection model is hostile to thousands of long-lived SSE streams; Django's ORM/admin batteries solve problems this design has already solved differently (hexagonal ports, RLS).

3. **Trade-offs accepted.** Python's runtime performance ceiling is real — mitigated by the I/O-bound profile and horizontal scaling; if a CPU hotspot emerges in the API tier (token counting is the likely one), it moves to a Rust extension (tiktoken already is one). Type safety is weaker than Go/Rust — mitigated by strict mypy in CI (NFR-M-1 adjacent) and Pydantic runtime validation at every boundary.
4. **Scalability limits.** Single uvicorn process: ~1–2K concurrent SSE streams (each ~tens of KB of asyncio state; memory, not CPU, binds first). NFR-S-1's 5,000 streams ⇒ 4–6 processes across replicas — trivial. The framework is never the bottleneck before the database or providers are.
5. **Bottlenecks.** Event-loop blocking is the classic self-inflicted one — policy: **no synchronous I/O or CPU work > 10 ms on the loop**, enforced by a blocking-call detector in dev/test and code review rules; CPU work goes to the worker or a bounded thread pool.
6. **Security.** Pydantic strict mode at every inbound boundary (no implicit coercion); dependency pinning + `pip-audit` in CI; slim non-root images (Ch. 3 Docker posture).
7. **Observability.** OTel auto-instrumentation for FastAPI/asyncpg/redis + manual spans for orchestration stages (per §3.8).
8. **Failure handling.** uvicorn graceful shutdown wired to the SSE drain sequence (§3.9.2); worker SIGTERM → finish-or-requeue (§3.2.8).
9. **Cost.** Marginal — Python's memory overhead (~150–300 MB/process) fits the $24/mo VPS envelope.
10. **3-year evolution.** The hexagon keeps the exit affordable: extracted services can be rewritten per-service in Go/Rust if profiling ever justifies it; the OpenAPI + event contracts are language-neutral by design.

### 4.1 Decision D4-2: API Paradigm — REST (resource-oriented) + SSE

1. **Why chosen.** The API has one first-party consumer (the SPA) and one external persona (Devon, FR-API-\*) who expects REST + OpenAPI. Resources here (workspaces, threads, documents) are genuinely resource-shaped; streaming is unidirectional (SSE, ADR-3.9). REST's alignment with HTTP semantics gives rate limiting, caching, authz, and observability per-route for free.
2. **Alternatives.**
   - **GraphQL.** Solves client-driven aggregation across many resources/teams — a problem this system does not have (one backend, one SPA, shallow view models). *Costs it would impose:* per-field authz complexity on top of RLS, query-cost analysis to prevent abuse (its own rate-limiting science), cache fragmentation, and a worse story for the external API persona. Rejected; revisit only if third-party consumers demonstrate real aggregation pain.
   - **gRPC.** Excellent for *internal* service-to-service contracts — which don't exist yet (D3-1). Browser support requires grpc-web proxying; external-developer ergonomics are worse. Deferred: gRPC is the pre-selected contract for any future service extraction (noted in ADR-4.2).
   - **tRPC.** TS-only end-to-end typing; couples the API to the frontend stack and excludes the external persona. Rejected outright for a public API.
3. **Trade-offs.** REST's under-fetching → a small number of purpose-built read endpoints (e.g., thread-with-recent-messages) rather than generic expansion; over-fetching bounded by explicit response schemas. Custom actions that don't map to CRUD use the `:verb` suffix convention (Google AIP-136): `documents:initiate`, `workspaces:export`.

### 4.2 API Conventions (the contract's constitution)

| Concern | Rule | Rationale / rejected alternative |
|---|---|---|
| Versioning | URL path `/v1`; **additive-only within a version** (new fields/endpoints never break clients); breaking ⇒ `/v2` + `Deprecation`/`Sunset` headers + 6-month window | Header versioning (cleaner URLs) rejected: hostile to curl-ability, caches, and support debugging. Stripe-style dated versions rejected: per-account version pinning is overhead justified only at Stripe's client diversity |
| IDs | **UUIDv7** everywhere client-visible | Auto-increment leaks business volume + enables enumeration; UUIDv4 fragments B-tree indexes; v7 is time-ordered (index-friendly, doubles as a stable pagination key) — ADR-4.3 |
| Pagination | **Cursor (keyset)** on `(created_at, id)`, opaque base64 cursor, `limit` ≤ 100, `next_cursor: null` terminates | Offset rejected: O(n) skip cost on hot tables *and* unstable under concurrent inserts/deletes (skipped/duplicated rows — correctness, not just performance) |
| Idempotency | Mutating POSTs accept `Idempotency-Key` (chat additionally uses client-generated `message_id` as a natural key); server stores key + request-body hash + response for 24 h; replay returns stored response + `Idempotent-Replay: true`; same key + different body ⇒ `409` | Matches the Ch. 3 retry table: clients may only retry POSTs because idempotency makes it safe — Stripe's model |
| Concurrency control | `ETag` + `If-Match` required on PATCH of mutable config (workspace settings, budgets, member roles); `428 Precondition Required` if absent | Lost-update prevention on admin surfaces; messages/documents are immutable so ETags are unnecessary there |
| Timestamps | RFC 3339 UTC, `*_at` suffix, server-assigned only | Client clocks are never trusted, including in cursors |
| Errors | RFC 9457 Problem+JSON (§3.6.1), machine-readable `code`, correlation ID always | — |
| Rate-limit signaling | IETF `RateLimit-*` draft headers + `Retry-After` on 429 | — |
| Field naming | `snake_case` JSON; enums as lowercase strings; booleans never nullable | Tri-state booleans are two booleans hiding in one |
| Deletion | `DELETE` returns `202 Accepted` + status URL when cascading async (documents, workspaces); `204` when instant | Honest about the deletion saga (DF-3) instead of pretending synchronicity |

### 4.3 Resource Catalog (complete v1 surface)

All routes below `/v1`. Auth column: `S`=session/JWT, `K`=API key allowed, `A`=admin/owner role. RL class: `auth` (strictest), `cheap` (reads), `heavy` (LLM/upload). Tenant-scoped resources live **under `/workspaces/{ws}`** deliberately — path-explicit tenancy (see F-2 in self-review).

| Resource | Endpoints | Auth | RL | Notes |
|---|---|---|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/oauth/{provider}:start`, `GET /auth/oauth/{provider}:callback` | — | auth | Enumeration-safe responses; refresh rotation w/ reuse detection (Ch. 7) |
| Me | `GET /me`, `PATCH /me`, `GET /me/workspaces` | S | cheap | |
| Workspaces | `POST /workspaces` · `GET/PATCH /workspaces/{ws}` · `DELETE /workspaces/{ws}` (202, cascade) · `POST /workspaces/{ws}:export` (202, job) | S; PATCH/DELETE/export: A | cheap | ETag on PATCH; export→FR-AD-5 |
| Members | `GET /workspaces/{ws}/members` · `PATCH /workspaces/{ws}/members/{user}` (role; ETag) · `DELETE …/{user}` | S; mutations: A | cheap | Last-owner protection (cannot demote/remove sole Owner) |
| Invitations | `POST /workspaces/{ws}/invitations` · `DELETE …/{id}` · `POST /invitations/{token}:accept` | A; accept: S | auth | Single-use, expiring, audit-logged |
| API keys | `POST /workspaces/{ws}/api-keys` · `GET` list (prefix+metadata only) · `DELETE …/{id}` | A | auth | Secret shown once at creation; hashed at rest |
| Threads | `POST /workspaces/{ws}/threads` · `GET` list (cursor, search) · `GET/PATCH/DELETE …/{thread}` | S,K | cheap | PATCH: title/settings |
| Messages | `POST /workspaces/{ws}/threads/{thread}/messages` (SSE response) · `GET` list (cursor) | S,K | **heavy** | `message_id` client-generated (idempotent); `Accept: text/event-stream` |
| Generations | `DELETE /workspaces/{ws}/generations/{gen}` (cancel) · `GET …/{gen}` (status) | S,K | cheap | Cancel via pub/sub (§3.2.3); DELETE-as-cancel: the generation *resource* is destroyed, not the message |
| Feedback | `POST …/messages/{msg}/feedback` | S | cheap | FR-CH-6 → eval corpus |
| Documents | `POST /workspaces/{ws}/documents:initiate` → presigned URL · `POST …:confirm` · `GET` list/status (cursor) · `GET …/{doc}` · `DELETE …/{doc}` (202, DF-3 cascade) | S,K; delete: role ≥ Member | heavy (initiate), cheap (reads) | Status = event projection (FR-KB-2) |
| Search | `POST /workspaces/{ws}/search` (explicit retrieval, no generation) | S,K | heavy | Raj's debug surface + external RAG-as-API |
| Usage & budget | `GET /workspaces/{ws}/usage` (rollups) · `GET/PUT /workspaces/{ws}/budget` (ETag) | S; PUT: A | cheap | FR-AD-2/3 |
| Audit | `GET /workspaces/{ws}/audit-events` (cursor, filters) | A | cheap | Read-only, immutable store |
| Health | `GET /healthz` (liveness) · `GET /readyz` (deps) | — (internal) | — | Unversioned; never public via edge |

**Route-count sanity check:** ~40 endpoints — small enough to hold in one head, complete enough to run the product. Every FR-\* marked MVP in Ch. 2 maps to at least one row above (traceability audit in §4.10).

### 4.4 Streaming Contract (the SSE surface, normative)

- **Request:** `POST …/messages` with `Accept: text/event-stream`; response is the event stream for that generation. (Clients preferring polling omit the header and get `202` + generation status URL — same pipeline, two consumption modes.)
- **Event grammar (from §3.6.1, now with payload semantics):** `meta` (generation_id, model, grounded flag — always first) → `token`* (delta text, monotonic `seq`) → `citation`* (chunk provenance, emitted when resolved) → `banner`? (degradation notices) → `usage` (final token/cost accounting) → `done{status: complete|partial|cancelled|error}`. `error{code}` may precede `done`. Every event carries `id:` = `{generation_id}:{seq}` enabling `Last-Event-ID` resume (buffer per §3.2.3).
- **Heartbeats:** SSE comment frames every 15 s defeat idle-timeout proxies; absence of heartbeat for 45 s is the client's signal to reconnect.
- **Ordering guarantee:** `seq` is strictly monotonic per generation; a resumed client discards duplicates by `seq` — at-least-once delivery, exactly-once rendering.

### 4.5 Backend Concurrency, Backpressure & Load Shedding

- **Pool math (the arithmetic juniors skip):** PgBouncer in transaction mode caps server connections at ~30 (VPS-sized PG). API replicas × per-replica pool must respect it: 2 replicas × 10 + workers × 5 ≈ 25 ✓. Scaling API replicas without re-doing this math is how Postgres falls over on launch day.
- **Bulkheads:** independent semaphores per dependency (PG, Redis, each provider, object store) — a hung provider exhausts its semaphore, not the process's capacity to serve health checks and cheap reads.
- **Backpressure & shedding order under saturation (explicit priority):** shed `heavy` ingestion initiations first (503 + Retry-After — upload retry is cheap), then new chat generations (429 queue-full), then cheap reads; **never** shed health probes, auth, or cancellation (cancellation under load is *more* important — it frees capacity). Brownout mode (sustained p95 breach): disable optional work (rerank, memory compaction) before rejecting users.
- **Timeout matrix (per dependency, hot path):** Redis 50 ms · PG statement 250 ms (hot) / 2 s (admin) · embedding call 2 s · provider connect 5 s, TTFT 10 s, inter-token idle 30 s · presign 1 s. Every outbound call has a deadline; deadlines compose within the 30 s route ceiling.

### 4.6 API Security Review — OWASP API Security Top 10 (2023) Mapping

| OWASP | Threat | Aether control |
|---|---|---|
| API1 BOLA | Object-level access bypass | Path-explicit tenancy + route guard asserting `path.ws == token.tenant` + RLS backstop + layer-8 canaries (§3.7.2) |
| API2 Broken authn | Token/credential weaknesses | Ch. 7 flows; short JWT TTL, rotation, reuse detection, lockouts |
| API3 Property-level | Mass assignment / excessive exposure | Pydantic: explicit request models (unknown fields rejected), explicit response models (nothing serialized by default) — allowlist both directions |
| API4 Resource consumption | Flood/oversize/expensive calls | RL classes, body caps, `max_tokens` ceilings, budgets, stream caps, timeout matrix |
| API5 Function-level authz | Missing role checks | **Deny-by-default router:** every route must declare required role/scope or registration fails at startup (a missing declaration is a boot error, not a vulnerability) — ADR-4.5 |
| API6 Sensitive business flows | Invite/export/key-creation abuse | `auth` RL class + audit + owner-role gating |
| API7 SSRF | Server-side fetch of user URLs | No user-supplied URL fetch in MVP; Phase 2+ connectors get allow-list + metadata-endpoint blocking |
| API8 Misconfiguration | Headers/CORS/debug leaks | Strict CORS (SPA origin only), security headers (CSP, HSTS), debug off in prod profile, config asserted by CI tests |
| API9 Inventory | Shadow/undocumented endpoints | OpenAPI generated from code = inventory by construction; CI test: every registered route appears in spec with auth + RL declared |
| API10 Unsafe 3rd-party consumption | Trusting provider responses | Router normalizes/validates provider output; provider JSON never passes through unvalidated (schema-checked before entering domain) |

### 4.7 Observability, Failure Scenarios, Cost (API-tier specifics)

- **Per-route RED** + per-route auth-failure and RL-rejection counters (a spike in 403s on one route is an attack signature, §1.10). Access log schema joins §3.8's structured format; slow-query log surfaces per-route p99 offenders weekly.
- **Failure scenarios rehearsed:** PgBouncer saturation → pool-wait metric alerts before user impact; a single hung provider → bulkhead contains, fallback chain routes (SD-1); replica OOM from SSE leak → per-process stream cap + memory limit + restart (streams resume via buffer); thundering reconnect after deploy → jittered client retry + `Retry-After` on 503.
- **Cost:** API tier adds no marginal infra beyond D3-1's envelope; the cost-relevant design choice here is presigned uploads (bandwidth bypasses compute) and cursor pagination (bounded query cost per request).

### 4.8 Three-Year Evolution (API surface)

Additive-only discipline makes v1 durable: expected additions are batch endpoints (`documents:batchInitiate`), partial responses (`?fields=`), workspace-level webhooks (Phase 2, HMAC-signed, retried per §3.6.2 worker policy, interface frozen now), agent-run resources (Phase 2: `POST /workspaces/{ws}/agent-runs`, trace retrieval — designed in Ch. 6, additive by construction), and — only if extraction happens — internal gRPC contracts mirroring the ports (ADR-4.2). `/v2` is a last resort with a named budget: one major version migration per two years, maximum.

### 4.9 Decision Records (Chapter 4)

| ADR | Decision | Status | Revisit trigger |
|---|---|---|---|
| ADR-4.1 | Python 3.12 + FastAPI + Pydantic v2; strict mypy; no blocking I/O on loop (D4-1) | Accepted | CPU profile shows API-tier hotspot un-fixable by extension |
| ADR-4.2 | REST + SSE public API; gRPC reserved for future internal seams; GraphQL declined (D4-2) | Accepted | Third-party aggregation demand materializes |
| ADR-4.3 | UUIDv7 for all client-visible IDs | Accepted | — |
| ADR-4.4 | Cursor/keyset pagination only; offset banned by lint on new endpoints | Accepted | — |
| ADR-4.5 | Deny-by-default route registration (undeclared authz = boot failure) | Accepted | — |
| ADR-4.6 | Idempotency-Key + body-hash + 24 h response replay; client `message_id` for chat | Accepted | — |
| ADR-4.7 | URL-path versioning, additive-only within version, 6-month Sunset on breaking | Accepted | — |

### 4.10 Interview Questions & Ideal Answers (Chapter 4)

**Q1. "Python has a GIL. Why is it acceptable for a high-concurrency streaming API?"**
*Ideal:* Separates concurrency (I/O multiplexing — asyncio handles thousands of idle-waiting streams on one thread) from parallelism (CPU — which this tier doesn't do; parsing lives in worker processes). Names the real risk — blocking the event loop — and the enforcement (detector + review rule + bounded thread pool). Names what would change the answer: CPU-heavy inference in-process.

**Q2. "Why cursor pagination? Offset is simpler."**
*Ideal:* Two independent arguments: performance (offset scans discarded rows — O(n) per page on hot tables) and *correctness* (offset under concurrent writes skips or duplicates rows — a consistency bug users see). Keyset on `(created_at, id)` with UUIDv7 gives a stable, indexed cursor. Bonus: cursors must be opaque so the sort key can evolve.

**Q3. "A client sends the same chat POST twice — network retry. What happens?"**
*Ideal:* Client-generated `message_id` is a natural idempotency key with a unique constraint; the second request returns the stored response (`Idempotent-Replay: true`) rather than double-charging tokens. Distinguishes from `Idempotency-Key` on non-natural-key routes; same key + different body = 409. Knows why this enables the retry policy at all.

**Q4. "How do you prevent a route shipping without authorization?"**
*Ideal:* Inverts the frame — prevention by construction, not review vigilance: route registration requires an authz declaration or the process fails to boot (ADR-4.5); CI asserts spec↔registry parity (API9). "We're careful" is the wrong answer; "it cannot boot" is the right one.

**Q5. "Your API is saturated. Walk me through what degrades and in what order."**
*Ideal:* Reproduces the shedding ladder (ingestion → new generations → cheap reads; never health/auth/cancel — cancel *frees* capacity), bulkheads per dependency, brownout of optional stages (rerank) before user-visible rejection, and the pool math that prevents self-inflicted DB collapse when scaling replicas.

### 4.11 Common Junior-Engineer Mistakes (backend/API)

1. Blocking calls (sync DB driver, `requests`, CPU loops) on the event loop — the #1 async-Python production killer.
2. Offset pagination on tables that grow — works in the demo, dies at page 4,000.
3. Auto-increment IDs in URLs — leaks volume, invites enumeration.
4. Authorization by convention ("we always add the decorator") instead of by construction.
5. Returning 200 with `{"error": ...}` — breaks every HTTP-aware layer above.
6. No `Retry-After`/jitter guidance → clients invent thundering herds.
7. Unbounded connection pools scaled per-replica until Postgres collapses — nobody did the multiplication.
8. Versioning nothing until the first breaking change, then breaking everyone at once.
9. Treating OpenAPI as post-hoc documentation instead of generated contract — guaranteeing drift.
10. Nullable booleans and stringly-typed enums — tri-state logic smuggled into the contract.

### 4.12 Production Best Practices Borrowed (API tier)

**Stripe:** idempotency keys with stored-response replay; additive evolution discipline. **Google AIP:** resource-oriented design, `:custom` verbs (AIP-136), standardized list/pagination semantics (AIP-158). **Microsoft REST Guidelines:** Problem+JSON errors, `Retry-After` everywhere retryable. **Amazon:** API-first mandate — the SPA holds no privilege the public API lacks. **Shopify/GitHub:** cost-classed rate limits and `Sunset`-header deprecation with real dates.

### 4.13 Design Review Checklist, Risks, Open Questions

**Checklist:** every MVP FR maps to a route (✓ audited against §2.1 — FR-CH-5/KB-6/KB-7/API-5 correctly absent as Phase 2); every route declares auth + RL class (✓ by construction, ADR-4.5); every mutation idempotent or guarded (✓ ETag/Idempotency-Key); every outbound call has a deadline (✓ §4.5 matrix); streaming contract fully specified incl. resume + ordering (✓ §4.4); shedding order documented (✓); OWASP API Top 10 mapped (✓); pool math done (✓). Open box: [ ] contract tests green against a running instance — implementation-dependent, tracked to Ch. 11.

**Risks:** FastAPI/Pydantic major-version churn (pin + quarterly upgrade window); SSE-over-HTTP/1.1 browser connection limits (mitigated — see F-1 below); idempotency store growth (24 h TTL + size alert).

**Open questions → later chapters:** OQ-4.1 → Ch. 5: does the SPA consume the search endpoint directly or only via chat? OQ-4.2 → Ch. 6: does `search` expose rerank scores externally (couples public contract to internal ranking)? OQ-4.3 → Ch. 7: cookie vs. bearer storage for the SPA session (continues OQ-3.3).

### 4.14 Self-Review Record — Chapter 4

**Coverage audit:** runtime decision (D4-1) resolves OQ-3.1 with 4 alternatives; paradigm decision (D4-2) with 3; conventions table covers versioning/IDs/pagination/idempotency/concurrency-control/errors/deletion; full v1 route catalog with auth+RL per route; normative streaming contract; concurrency/backpressure/shedding with arithmetic; OWASP mapping; ADRs ×7; interview/mistakes/best-practices/checklist present. Second-pass findings:

| Finding | Severity | Resolution |
|---|---|---|
| F-1: **SSE over HTTP/1.1 hits the browser's ~6-connections-per-origin cap** — a user with several tabs open silently loses streams; first draft never addressed transport | **High** | Edge terminates **HTTP/2** (multiplexed streams share one connection); compose demo profile configures h2; documented as a hard requirement for the reverse proxy, added to §3.9.1 assumptions via this record |
| F-2: Tenancy was implicit (from token) in an early route sketch — a confused-deputy risk and unfriendly to audit; also made API-key scoping ambiguous | Medium | Routes made **path-explicit** (`/workspaces/{ws}/…`) with a route guard asserting path-tenant ≡ credential-tenant (mismatch → 403 + audit event); this also gives API keys an unambiguous scope anchor |
| F-3: Generation cancel was styled `POST :cancel` in draft, inconsistent with resource semantics | Low | Modeled as `DELETE /generations/{gen}` — the generation is a resource whose destruction is cancellation; status remains GET-able |
| F-4: Draft shedding order sacrificed cancellation under load — exactly when cancellation is most valuable (it releases provider capacity) | Medium | Cancellation promoted to never-shed class alongside health and auth |
| F-5: Idempotency replay semantics under-specified (what if same key, different body?) | Low | 409 on key/body-hash mismatch; `Idempotent-Replay: true` header on replays; 24 h retention — now normative in §4.2 |

**Verdict:** Chapter 4 passes self-review. F-1 was the genuine catch — a transport-level constraint that invalidates the multi-tab UX silently and is invisible in any diagram; it now binds the edge configuration (Ch. 10 inherits it as a deployment requirement).

---

