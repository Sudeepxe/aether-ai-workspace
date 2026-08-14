# ADR-6.3: Hybrid RRF retrieval for MVP; reranking gated on a 5-point eval gain; condensing rewrite with dual-feed to lexical and vector legs

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Resolves open question OQ-1 (is reranking in the MVP retrieval path?) and addresses multi-turn follow-up queries, which embed poorly when taken literally (for example, "what about the second one?").

## Decision

MVP retrieval is hybrid: HNSW vector search (k=20) and Postgres full-text search (k=20), fused via Reciprocal Rank Fusion, then MMR de-duplication, with the top 6 results entering the prompt. A condensing rewrite step (a cheap model, within a 150ms budget) produces a standalone query for follow-up turns; both the raw and rewritten queries feed the lexical leg, while only the rewritten query feeds the vector leg. Cross-encoder reranking is deferred to Phase 2, behind a flag, adopted only if eval data shows at least a 5-point faithfulness or precision gain.

## Alternatives considered

- **Vector-only retrieval** — hybrid was chosen because lexical search rescues exactly what embeddings fumble — IDs, part numbers, names, acronyms — for the cost of one extra indexed query.

- **No query rewrite** — fails follow-up queries; kept as the eval control.

- **Concatenated-history embedding** — noise swamps signal.

- **HyDE (hallucinate-then-embed)** — adds latency and cost for corpus-dependent gains; flagged as a Phase 3 experiment.

## Consequences

Easier: hybrid retrieval is cheap and robustly better than either leg alone; reranking's adoption is evidence-gated rather than assumed, avoiding premature complexity. Harder: an early draft fed only the rewritten query to both retrieval legs, which would have silently defeated hybrid's purpose by discarding exact terms the lexical leg exists to catch — corrected via the dual-feed design recorded in the chapter's self-review.

## Revisit trigger

Eval evidence, specifically for reranking adoption.
