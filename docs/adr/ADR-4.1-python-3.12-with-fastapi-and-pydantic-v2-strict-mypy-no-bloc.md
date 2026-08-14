# ADR-4.1: Python 3.12 with FastAPI and Pydantic v2, strict mypy, no blocking I/O on the loop (D4-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Resolves open question OQ-3.1 — the API tier's runtime/framework/language choice, given the worker side must run Python regardless (parsers, tokenizers, and the RAG-eval ecosystem are Python-first) and the chat workload is I/O-bound end-to-end.

## Decision

Python 3.12+, FastAPI on uvicorn (asyncio), Pydantic v2 as the single validation/serialization contract; one codebase produces both the api and worker processes (D3-1). Strict mypy is required, and no blocking I/O or CPU work over 10ms is allowed on the event loop, enforced by a blocking-call detector in dev/test.

## Alternatives considered

- **Go (chi/echo)** — the strongest rival — better raw concurrency, single-binary deploys, lower memory — but it forces a two-codebase split from the Python worker, and its AI-adjacent ecosystem is thin; noted as a natural choice for an extracted LLM Router at 100x scale with a team.

- **TypeScript (NestJS/Fastify)** — the same worker-ecosystem problem as Go applies, and end-to-end type sharing is achievable anyway via OpenAPI-generated TypeScript types.

- **Rust (Axum)** — performance the envelope does not need, at a development-velocity cost the schedule cannot pay.

- **Django/Flask (synchronous Python)** — WSGI's thread-per-connection model is hostile to thousands of long-lived SSE streams.

## Consequences

Easier: FastAPI generates the OpenAPI 3.1 contract from the same Pydantic models that validate requests, making the published spec a build artifact instead of a maintenance chore. Harder: Python's runtime performance ceiling is real (mitigated by the I/O-bound profile and horizontal scaling; a CPU hotspot would move to a Rust extension); type safety is weaker than Go/Rust (mitigated by strict mypy plus Pydantic runtime validation).

## Revisit trigger

CPU profiling shows an API-tier hotspot that cannot be fixed by extension.
