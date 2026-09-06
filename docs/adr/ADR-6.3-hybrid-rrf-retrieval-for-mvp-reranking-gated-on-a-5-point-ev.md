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

## Phase 6 update (real sweep data, docs/REMEDIATION_PLAN.md)

Real parameter sweeps against the v2 golden set
(`evals/golden/v2/PHASE6_RESULTS.md`) tested this decision's own
constants directly. Read together with a constraint stated up front in
that report: every number below comes from `LocalHashEmbeddingAdapter`
— a real, deterministic, but **non-semantic** embedder (hash expansion
of a text's own bytes), the only one configured in this environment.

**RRF constant and MMR λ: a null result, not a tuning decision.**
Sweeping the RRF constant across 10/60/100 and MMR λ across 0.5/1.0
produced *zero difference on every retrieval metric measured*
(recall@5, recall@10, MRR, NDCG@10, near-miss recall@10) — see PHASE6's
Priority 4 table. This is not "these constants don't matter" as a
general claim about hybrid RRF retrieval; it is specific to this
embedder. With a non-semantic embedder, the vector leg's cosine scores
carry no real relevance signal, so its contribution to both RRF fusion
and MMR's similarity-based diversity penalty is close to noise relative
to the lexical leg's real term-match ranking — changing how strongly
RRF/MMR weight an already-noisy signal doesn't change which chunks end
up on top, because the noise was never driving the outcome. Neither
constant was changed in `hybrid_search.py`; retesting this specific
finding requires a real semantic embedder (deferred — no local
sentence-transformers adapter or OpenAI key configured this session;
ADR-8.4 gates that migration on this exact recall-verification work).

**Chunking and candidate-limit findings, same corpus (real, not null):**
larger chunks (768/1000 target/max, same 12.5% overlap) measured
recall@10 = 90.0% versus baseline's 66.2% — a real, meaningful
difference, not noise — but the corpus is only 6 documents / 14 chunks
at that setting, close enough to "one chunk per document" that some of
the improvement is plausibly a small-corpus artifact rather than a
universally better chunking strategy; not applied to `chunking.py`
pending a larger golden set. Separately, shrinking MMR's own candidate
pool to 10 dropped MRR from 56.6% to 43.1% even though recall@10 was
unaffected — a real ranking-quality cost a recall-only metric would
have missed, and evidence this decision's `limits=20`/`k=10` production
defaults are doing real work, not just headroom.

**What would need to change:** a real semantic embedding model, so the
vector leg's cosine similarity carries actual relevance signal instead
of noise the fusion/diversity math is currently averaging away. Until
then, tuning RRF's constant or MMR's λ against this specific setup
would be tuning against an artifact of the embedder, not this
decision's architecture.
