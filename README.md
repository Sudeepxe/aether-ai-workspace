# Aether AI Workspace

[![ci](https://github.com/Sudeepxe/aether-ai-workspace/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sudeepxe/aether-ai-workspace/actions/workflows/ci.yml)

**A production-grade, multi-tenant AI workspace platform** — RAG-grounded
chat over private knowledge bases, with measured faithfulness *and* measured
refusal. Built end-to-end by one engineer as an architecture-first flagship:
the AI features are one subsystem inside a real production application
(auth, tenancy, budgets, audit, observability, DR) — not the whole app.

> **Status: Sprint 8 — Memory, feedback, and provable deletion complete.**
> Sprints 0–7 (factory; identity & forced row-level-security tenant
> isolation; workspace/membership/invitation CRUD, RBAC, audit logging,
> email, rate limiting; the real-time streaming spine with cross-replica
> resume/cancel; the LLM Router with usage/budget admission; the full
> knowledge-base ingestion pipeline; grounded chat — hybrid retrieval,
> RRF/MMR, dual-feed query rewrite, two-gate refusal, per-chunk citations,
> citations/refusal UI; the real evaluation harness with a live 20-case
> golden-set baseline) are merged to `main`. Sprint 8 completes the data-
> lifecycle story (§1.5, FR-AD-5, DF-3): a token-budgeted thread-window +
> rolling-compaction memory service with an honest window-only fallback on
> compactor failure; message-level 👍/👎 feedback capture threaded onto
> `GET .../messages`; a real async workspace-deletion saga (`202` + job
> polling, outbox-driven worker purge, atomic hard-delete cascade across
> every tenant-scoped table); a real tenant-data-export saga (JSON +
> original files zipped, presigned download); and — the sprint's literal
> exit criterion (§11.6) — an **independent** deletion-verification job
> that re-checks residue across all 11 tenant-scoped tables plus real
> object storage, never trusting the deletion saga's own self-report.
> `test_FR_KB_5_deletion_cascades` passes for real against real
> Postgres/MinIO, and a real stray-object scenario proves the verifier
> actually detects residue rather than rubber-stamping. The one number
> this project still does **not** claim: the North Star (§1.7, faithfulness
> ≥ 90% ∧ correct-refusal ≥ 90%) remains **not yet determinable** — it
> needs a real cross-family LLM judge, and no OpenAI/Anthropic key is
> provisioned in this environment. Every AI-facing component (chat
> generator, embedding call, query rewrite, memory compaction) likewise
> falls back to honest, clearly-labeled local placeholders in dev/CI — real
> providers activate automatically the moment the corresponding API key is
> set, no code change required. This README is honest about state: no fake
> badges, no aspirational numbers.
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
| One-command demo | infra: `make dev` today · full demo profile: S9–S11 |

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
