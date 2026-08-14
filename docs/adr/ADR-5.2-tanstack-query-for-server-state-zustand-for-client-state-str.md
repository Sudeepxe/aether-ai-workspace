# ADR-5.2: TanStack Query for server state, Zustand for client state; streaming bypasses the cache with an rAF-batched buffer (D5-2)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A token-streaming chat UI has two very different kinds of state — server-owned data (threads, documents, usage) and small, genuinely local UI state (composer draft, active stream buffer) — and conflating them causes both poor caching semantics and re-render storms during streaming.

## Decision

TanStack Query owns all server state, fetched, cached, invalidated, and retried per the API's idempotency and retry semantics. Zustand owns the small residue of true client state. No global store holds server data, ever. In-flight token streams deliberately bypass the query cache — tokens append to a Zustand buffer flushed in requestAnimationFrame batches (roughly 30fps) to the single active message component; on stream completion, the settled message is written into the TanStack cache.

## Alternatives considered

- **Redux (with RTK Query)** — its value is centralized complex client state, which this app does not have, and RTK Query duplicates TanStack Query with more ceremony.

- **React Context for everything** — context invalidates whole subtrees per update, unusable at token-streaming rates.

- **MobX** — a fine engine with weaker ecosystem pull and no differentiated win over the chosen combination.

## Consequences

Easier: server state gets correct caching and invalidation semantics for free; the streaming exception keeps a 100-token-per-second stream from causing 100 re-renders per second. Harder: developers must remember the streaming path is a deliberate, documented exception to the "server state lives in TanStack Query" rule, not an inconsistency.

## Revisit trigger

None stated.
