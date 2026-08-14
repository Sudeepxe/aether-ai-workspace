# ADR-3.1: Modular monolith plus async workers; Kubernetes-ready, not distributed (D3-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The Chapter 1 scale envelope (100 tenants, 50 concurrent streams, 60 docs/min) needed an architectural style decision for one engineer building a system with real streaming and async-ingestion workloads.

## Decision

Aether ships as two application processes built from one codebase — a stateless API service and a background worker — plus managed data services (PostgreSQL, Redis, object storage). The codebase is internally decomposed into strict logical services (the §3.2 catalog) with enforced module boundaries, so any logical service can be extracted to a network service later without redesign.

## Alternatives considered

- **Full microservices (one deployable per catalog service)** — résumé-driven architecture at this scale; adds service-mesh, contract-versioning, and distributed-tracing overhead that exceeds one engineer's maintenance capacity, and each network hop spends the latency budget.

- **Serverless (FaaS plus managed queues)** — long-lived SSE streams fight FaaS timeout models; cold starts eat the time-to-first-token budget; breaks local-first portability.

- **Actor-model runtime (Elixir/OTP, Akka, Orleans)** — the most defensible rejected alternative — supervision trees map beautifully to streaming chat — but the AI/RAG library ecosystem (parsers, embeddings, eval tooling) lives in Python/TypeScript, and fighting that ecosystem costs more than actor supervision buys.

- **Single monolith with no workers (ingestion in-request)** — rejected outright: violates async ingestion, ties 50MB PDF parsing to HTTP timeouts, and head-of-line-blocks chat traffic.

## Consequences

Easier: the envelope fits comfortably in one horizontally replicated stateless process, and the shape keeps option value — boundaries are logical today, physical tomorrow if needed. Harder: shared blast radius inside the API process (mitigated by the api/worker process split, per-container resource limits, module-level circuit breakers); a single shared Postgres invites noisy-neighbor effects (mitigated by RLS, per-tenant rate limits, statement timeouts, PgBouncer).

## Revisit trigger

Team grows beyond ~3 engineers, or sustained load exceeds ~10x the design envelope.
