# ADR-11.2: The search endpoint's relevance scores are an unstable, versioned contract

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Resolves open question OQ-4.2, left unresolved through Chapters 4 through 10 — does the POST /search endpoint, used both as a debug surface and as an external RAG-as-API capability, expose internal ranking scores, and if so, does that couple the public API contract to the internal ranking implementation such as RRF fusion or a future reranker?

## Decision

search returns fused relevance scores as explicitly documented-unstable, dimensionless values, carrying a score_version field. External consumers may use the score to order results but must not persist it or threshold on it as if it were a stable metric. Reranking internals, planned for Phase 2, never leave this boundary.

## Alternatives considered

- **Exposing no score at all** — rejected, since the debug surface specifically needs to show raw retrieval scores.

- **Exposing a stable, versionless score as a firm contract** — rejected — this would freeze the internal ranking stack, such as RRF weights and future rerank scoring, against future improvement, since any change to ranking internals would become a breaking API change.

## Consequences

Easier: internal retrieval-ranking evolution, such as adopting reranking in Phase 2 once it clears its evidence gate, remains free to happen without an API version bump. Harder: external consumers get a genuinely less stable value to work with — an explicit, documented trade rather than a false promise of stability.

## Revisit trigger

None stated.
