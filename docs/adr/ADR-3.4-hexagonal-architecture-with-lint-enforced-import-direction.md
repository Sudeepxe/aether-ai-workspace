# ADR-3.4: Hexagonal architecture with lint-enforced import direction

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The "distributed-ready, not distributed" claim (D3-1) needs to be a structural fact rather than an aspiration — the internal codebase organization must make provider swaps, future service extraction, and fast domain testing genuinely possible, not just theoretically possible.

## Decision

Hexagonal (ports and adapters) architecture with enforced import direction: domain imports nothing but itself; the application layer imports domain and ports; adapters import ports only; nothing imports adapters except the composition root. The rule is lint-enforced — a build fails on violation, not just a code-review convention.

## Alternatives considered

- **No enforced internal layering (organize by convention only)** — not separately argued as a competing style in the text; hexagonal was adopted specifically as the implementation-level counterpart that makes D3-1's process-level modular-monolith decision real rather than aspirational.

## Consequences

Easier: the eval suite tests orchestration logic against fake LLMPort/VectorSearchPort implementations — deterministic, free, fast; a provider swap is a new adapter with zero domain change; a future service extraction becomes reimplementing a port over HTTP/gRPC with callers unable to tell the difference. Harder: every layer boundary must be actively respected — enforced early via import-boundary lint before it can decay.

## Revisit trigger

None stated — a structural rule, not a scale-triggered decision.
