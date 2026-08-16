# Aether AI Workspace

[![ci](https://github.com/Sudeepxe/aether-ai-workspace/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sudeepxe/aether-ai-workspace/actions/workflows/ci.yml)

**A production-grade, multi-tenant AI workspace platform** — RAG-grounded
chat over private knowledge bases, with measured faithfulness *and* measured
refusal. Built end-to-end by one engineer as an architecture-first flagship:
the AI features are one subsystem inside a real production application
(auth, tenancy, budgets, audit, observability, DR) — not the whole app.

> **Status: Sprint 4 — Router & Budgets complete.** Sprints 0–3 (factory;
> identity & forced row-level-security tenant isolation; workspace/membership/
> invitation CRUD, RBAC, audit logging, email, rate limiting; the real-time
> streaming spine with cross-replica resume/cancel) are merged to `main`.
> Sprint 4 replaces the Sprint 3 echo placeholder with a real LLM Router —
> OpenAI/Anthropic provider adapters normalized to one internal stream
> schema, per-provider circuit breakers, fallback-chain routing, and
> per-provider concurrency semaphores (one tenant's burst can't saturate a
> shared provider connection pool) — plus a usage ledger and hybrid budget
> admission/settlement model: a pre-request ceiling-estimate check (global
> $50/mo kill switch + per-workspace budgets, fail-closed on an unconfigured
> budget) refuses a request with 429 `budget_exhausted` before any provider
> call, and real post-generation cost settles from the provider's own
> authoritative token counts, not a local estimate. `GET /usage` and
> `GET/PUT /budget` (ETag, Admin-only PUT) plus a frontend usage indicator
> round out the surface. Real API keys aren't provisioned yet, so the app
> still runs on the Sprint 3 echo generator in dev/CI by default — the
> Router activates automatically the moment `AETHER_OPENAI_API_KEY`/
> `AETHER_ANTHROPIC_API_KEY` are set, no code change required. This README
> is honest about state: no fake badges, no aspirational numbers.
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
| **Eval score (faithfulness / refusal)** | measured from S7 — placeholder until then, never faked |
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
