# ADR-8.2: Messages stored as rows with a transactional per-thread seq (D8-2, resolves OQ-3.4)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Resolves open question OQ-3.4 — how conversation messages are stored and ordered, given that client clocks and even server timestamps can tie or skew under concurrency, and the streaming resume and pagination story needs a stable, gapless ordering key.

## Decision

Messages are stored as relational rows with a per-thread monotonic seq, assigned transactionally at insert via an explicit per-thread counter row locked FOR UPDATE within the insert transaction; a unique constraint on (thread_id, seq) enforces it.

## Alternatives considered

- **Timestamp ordering** — rejected as the primary ordering key due to ties under concurrency and skew under clock drift, though kept as display metadata.

- **JSONB message arrays per thread** — one row per thread means row-rewrite amplification per new message, no per-message foreign keys for citations or feedback, no partial indexing, and lock contention on hot threads.

- **Append-only event log (messages as events, threads as projections)** — rejected as the primary store despite architectural elegance, because every read would become a projection problem when the product's actual read pattern is paginated history — judged unjustified complexity.

## Consequences

Easier: seq gives gapless, clock-independent ordering, a natural pagination cursor, and the join key for Last-Event-ID streaming-resume reconciliation. Harder: a naive max(seq)+1 approach is a race under concurrent inserts to the same thread, resolved via the explicit locked counter row, with contention scoped to a single thread — a fix made in the chapter's own self-review.

## Revisit trigger

None stated.
