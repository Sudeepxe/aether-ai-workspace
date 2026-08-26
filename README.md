# Aether AI Workspace

[![ci](https://github.com/Sudeepxe/aether-ai-workspace/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sudeepxe/aether-ai-workspace/actions/workflows/ci.yml)

**A production-grade, multi-tenant AI workspace platform** — RAG-grounded
chat over private knowledge bases, with measured faithfulness *and* measured
refusal. Built end-to-end by one engineer as an architecture-first flagship:
the AI features are one subsystem inside a real production application
(auth, tenancy, budgets, audit, observability, DR) — not the whole app.

> **Status: Sprint 10 — Security hardening & API polish complete.**
> Sprints 0–9 (factory; identity & forced row-level-security tenant
> isolation; workspace/membership/invitation CRUD, RBAC, audit logging,
> email, rate limiting; the real-time streaming spine with cross-replica
> resume/cancel; the LLM Router with usage/budget admission; the full
> knowledge-base ingestion pipeline; grounded chat — hybrid retrieval,
> RRF/MMR, dual-feed query rewrite, two-gate refusal, per-chunk citations,
> citations/refusal UI; the real evaluation harness with a live 20-case
> golden-set baseline; memory service + message feedback; async workspace
> deletion/export sagas with independent deletion verification; real
> OpenTelemetry tracing + Prometheus/Grafana + alerting + chaos-lite + k6
> budgets) are merged to `main`. Sprint 10 closes the last MVP-committed
> API-surface gap and adds the security-hardening lane the blueprint
> promised but hadn't built yet: workspace-scoped, hashed, explicitly-
> scoped **API keys** (FR-API-2, §7.4) authenticating the chat surface
> alongside JWT sessions behind one unified authorization model; a generic
> **`Idempotency-Key`** replay mechanism (ADR-4.6) for plain mutating
> POSTs, reusing existing Redis infra; **OpenAPI publishing** — the spec
> is a real generated-and-diffed artifact (`packages/contracts/openapi.json`)
> with a `schemathesis`-driven contract-conformance CI gate, not aspirational
> documentation; an **OWASP ZAP baseline DAST scan** wired into CI against
> the real running API (two real header-hardening gaps it found are now
> fixed, not just allowlisted); a **secrets-rotation drill** covering all
> three secret classes this system has (SOPS/age recipient, provider API
> key, JWT signing key via a real kid-overlap mechanism), the SOPS/age half
> proven by a real add-then-remove rotation script that runs in CI on every
> PR; and the **Devon-persona quickstart** (§11.6's exit criterion) — a
> real external integrator's cold-start path, CI-verified end-to-end on a
> weekly schedule, confirmed green on a real run against `main`
> (register → real document ingestion → API-key-only grounded chat
> completion in ~6.5s). Several genuine bugs were found and fixed by
> actually running things rather than assuming: an asyncpg parameter-type-
> inference bug in the API-key last-used-timestamp coarsening SQL; a real
> functional bug where the chat rate limiter still hard-required a JWT
> even after the auth layer became API-key-aware; a `schemathesis`
> dependency pin that silently forced a CVE-carrying `starlette` downgrade
> across the whole dependency tree, caught by CI's own `pip-audit` gate;
> and a `sops` config-discovery quirk that broke the rotation drill's own
> first draft. The one number this project still does **not** claim: the
> North Star (§1.7, faithfulness ≥ 90% ∧ correct-refusal ≥ 90%) remains
> **not yet determinable** — it needs a real cross-family LLM judge, and
> no OpenAI/Anthropic key is provisioned in this environment. Every
> AI-facing component (chat generator, embedding call, query rewrite,
> memory compaction) likewise falls back to honest, clearly-labeled local
> placeholders in dev/CI — real providers activate automatically the
> moment the corresponding API key is set, no code change required. This
> README is honest about state: no fake badges, no aspirational numbers.
>
> **Branch protection note:** required status checks on `main` are enforced
> manually (every merge verifies all CI jobs green before squashing) rather
> than by GitHub's branch-protection API, which this private repo's plan
> tier doesn't support (`403 Upgrade to GitHub Pro...`) — tracked as an open
> owner decision in [issue #3](https://github.com/Sudeepxe/aether-ai-workspace/issues/3)
> (make the repo public, or upgrade the plan). The badge above tracks `main`
> and reflects the same CI lanes run on every PR.

## Architecture

Full blueprint: [`docs/architecture/`](docs/architecture/) — 11 reviewed
chapters, [56 ADRs](docs/adr/) with rejected alternatives, threat model, and
a signed final review. One-paragraph version: a **modular monolith** (API + worker
from one codebase) with lint-enforced hexagonal boundaries; **PostgreSQL +
pgvector** with row-level-security tenant isolation; **Redis Streams + a
transactional outbox** for eventing; **SSE streaming** with cross-replica
resume; a thin owned **LLM router** with fallback chains; and a CI-gated
**eval harness** (faithfulness ∧ correct-refusal ≥ 90% target).

## Proof (lights up as sprints land)

| Artifact | Status |
|---|---|
| CI pipeline | live (S0) — full shape, future lanes disabled-and-dated; verified green pre-merge (see branch protection note above) |
| Coverage gate | live (S1) — 80% minimum, enforced independently on unit+architecture and on integration+security |
| Auth & tenant isolation | live (S1) — EdDSA-JWT + rotating refresh tokens, forced RLS, three-role DB privilege model |
| Workspace CRUD, RBAC, audit log, email, rate limiting | live (S2) |
| Streaming chat (SSE, cross-replica resume/cancel) + SPA | live (S3) |
| LLM Router (OpenAI/Anthropic, breakers, fallback, concurrency limits) + usage/budget admission | live (S4) — falls back to S3's echo generator until real provider keys are provisioned |
| Knowledge-base ingestion (schema, object storage, fair-queued pipeline, malware scan, chunking, embedding) + document CRUD & upload UI | live (S5) — falls back to a local, honest, non-semantic embedder until real provider keys are provisioned |
| Grounded chat (hybrid retrieval + RRF/MMR, query rewrite, two-gate refusal, per-chunk citations) + citations/refusal UI | live (S6) — real end-to-end proof: a real ingested document produces a real cited answer, a real out-of-KB query refuses, both in a real browser |
| Eval harness + golden set v1 (20 cases) + CI tiers (path-filtered smoke, nightly) | live (S7) — [latest real report](docs/evals/latest-report.md) |
| **Eval score — refusal correctness / retrieval hit-rate / citation precision+recall** | **live, 100%** (S7, real, measured) — [report](docs/evals/latest-report.md) |
| **Eval score — faithfulness / North Star (§1.7)** | not yet determinable — needs a real cross-family LLM judge key, not configured in this environment; honestly reported, never faked |
| Memory service (thread window + rolling compaction) + message feedback (👍/👎) | live (S8) |
| Workspace deletion saga (DF-3, async `202`+job) + tenant data export (FR-AD-5) | live (S8) — real MinIO archive, real presigned download |
| **`test_FR_KB_5_deletion_cascades` + independent deletion-verification job (NFR-PR-1, §11.6 exit criterion)** | **live, green** (S8) — real residue sweep across every tenant-scoped table + object storage, proven to genuinely detect a stray-object scenario, not a rubber stamp |
| OpenTelemetry tracing + Prometheus metrics + LGTM stack (`make dev-observability`) | live (S9) — real correlated traces API→outbox→worker, live-verified end-to-end into Tempo |
| 5 Grafana dashboards (SLO, AI-plane, Ingestion, Data-tier, Cost), provisioned as code | live (S9) — every panel a real metric; honest notes where a signal isn't live yet |
| 11 Prometheus alert rules + runbooks + blameless postmortem template | live (S9) — `promtool` burn tests green in CI; a real alert live-verified firing end-to-end into a real inbox |
| Chaos-lite suite (Redis kill, worker-kill-mid-ingest, provider-mid-stream) | live (S9) — 3 real degraded-mode experiments against real killed/restarted containers, opt-in nightly lane |
| k6 performance budgets (NFR-P-1/2/3) in CI (`perf-smoke` + nightly) | live (S9) |
| API keys (FR-API-2, §7.4) — hashed, workspace-scoped, explicitly-scoped, authenticating chat alongside JWT | live (S10) — real HTTP round trip: API key alone authenticates, wrong-scope 403s, revoked 401s, cross-workspace 404s |
| Generic `Idempotency-Key` support (ADR-4.6) on plain mutating POSTs | live (S10) — real replay + real 409 on body mismatch, verified over real Redis |
| OpenAPI publishing (`packages/contracts/openapi.json`) + `contract` CI gate (diff + schemathesis) | live (S10) — generated artifact, diffed in CI; real conformance scan across every operation |
| OWASP ZAP baseline DAST scan in CI | live (S10) — real scan against the real running API on every PR; 2 real findings fixed for real, 3 justified-and-documented allowlist entries |
| Secrets-rotation drill (SOPS/age, provider key, JWT kid overlap) + runbook | live (S10) — SOPS/age half CI-verified on every PR (real add-then-remove rotation, scratch files); JWT kid overlap proven by real signer tests |
| **Devon-persona quickstart (§11.6 exit criterion: "works cold")** | **live, green** (S10) — real external-integrator cold start, weekly CI-verified: register → real document ingestion → API-key-only grounded chat completion, ~6.5s end to end |
| One-command demo | infra: `make dev` today · full demo profile: S11 |

## Quickstart (Sprint 0 scope)

```
make bootstrap   # toolchain check, deps, hooks, infra pull  (≤ 15 min, CI-verified monthly)
make dev         # healthchecked dev infra: Postgres+pgvector, Redis, MinIO, mailpit
make lint typecheck test
```

## Roadmap

GitHub milestones S0–S12 mirror the blueprint's implementation roadmap
(§11.6): identity → tenancy → streaming spine → router/budgets → ingestion →
grounded chat → **evals** → memory/deletion → observability → hardening →
prod/DR → v1.0 (production-readiness review).

## Limitations & gap register

Deliberate v1 boundaries, published not hidden (Blueprint §10.6): MFA
deferred to Phase 3 · single-node demo topology (99.0% SLO tier, HA path
documented) · best-effort on-call · no multi-region. Each carries a
pre-committed upgrade trigger.

## License · Security · Contributing

[Apache-2.0](LICENSE) · [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
