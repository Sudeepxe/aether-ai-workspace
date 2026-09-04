# Golden set v2 — retrieval ranking (Phase 5, docs/REMEDIATION_PLAN.md)

53 queries against one shared, fully-populated workspace (not v1's
per-case isolated workspaces), evaluated directly against
`HybridSearch.search()` — not through a chat turn. This closes two
methodological gaps the external audit found in v1:

1. v1's only retrieval signal was document-level citation hit-rate (did
   the model's final citations include the expected document), which
   can't demonstrate ranking quality and says nothing about chunks the
   model didn't cite. v2 grades the raw ranked chunk list itself, and
   reports real recall@5, recall@10, MRR, and NDCG@10 — genuine ranking
   metrics, computed with no LLM in the loop at all.
2. Every v1 unanswerable case used an empty knowledge base, a
   documented, known-reliable-but-untested-elsewhere refusal trigger.
   v2's `unanswerable_populated` class asks plausible-sounding questions
   against the real, full, competing 6-document corpus instead.

Run it: `cd apps/api && PYTHONPATH=../.. uv run python -m evals.harness.retrieval_cli run`
against a running dev stack (`make dev`, migrated, bucket provisioned).
Add `--verbose` for per-query detail, `--report-json <path>` for a
machine-readable report.

## Corpus

Six documents under `evals/corpora/v2/`, ~1,000–1,350 words each,
covering six real Aether subsystems (retrieval/refusal, the LLM router,
auth/sessions, workspace tenancy/budgets, the ingestion pipeline,
observability/SLOs) — drawn from this codebase's own real architecture,
not a fictional-company stand-in and not generated filler. Real chunk
distribution at this repo's actual chunking config (~512-token target,
800 hard ceiling, 10–15% overlap), verified by real ingestion, not
assumed:

| Document | Chunks |
|---|---|
| auth-and-sessions.md | 3 |
| ingestion-pipeline.md | 3 |
| llm-router-and-providers.md | 4 |
| observability-and-slos.md | 3 |
| retrieval-and-refusal.md | 4 |
| workspace-tenancy-and-budgets.md | 3 |
| **Total** | **20** |

Deliberately written so the same underlying mechanism (the pre-request
budget cost estimate, the presigned-URL download pattern, the
asynchronous-job pattern) is described from two different documents'
angles in places — real cross-document ambiguity a fictional, siloed
corpus wouldn't produce, and exactly what the near-miss class needs.

## Query classes

| Class | Count | What it tests |
|---|---|---|
| `answerable` | 35 | 1–2 graded-relevant chunks each (primary/supporting) |
| `near_miss` | 8 | A real, correct chunk exists *and* a plausible, semantically-adjacent wrong chunk exists elsewhere in the corpus |
| `unanswerable_populated` | 10 | Plausible-sounding, no real answer anywhere in the 6 docs — asked against the full populated KB, not an empty one |

45 real answerable/near-miss query→chunk relevance pairs (not padded to
hit a target number — this is what was actually written and verified).

**Ground truth mechanism:** chunk ids are random UUIDs regenerated every
ingestion run, so golden queries can't name one directly. Each relevant
chunk is instead identified by an *anchor* — a substring from the
intended answer, resolved against real ingested chunk content at eval
time (`retrieval_runner.build_anchor_index`), never hardcoded. 8 of the
45 pairs resolve to *two* real chunk ids, not a bug: the chunker's real
10–15% overlap genuinely duplicates the anchor sentence across two
adjacent chunks (verified by inspecting real ingestion output before
finalizing the golden set — see `answerable-05-key-rotation-overlap`'s
`notes` field for the clearest example). Retrieval is scored as correct
if *either* copy is found.

## Real results (this run, this environment, 2026-09-04)

```
--- answerable (35 queries) ---
recall@5:   59.5%
recall@10:  66.2%
MRR:        56.6%
NDCG@10:    57.2%

--- near-miss (8 queries) ---
recall@10:                 62.5%
distractor beats truth:    33.3%

--- unanswerable, populated KB (10 queries) ---
refusal-threshold correct: 0.0%
```

These are real, measured numbers from a real run against this repo's
real retrieval pipeline (real Postgres HNSW + full-text search, real
RRF, real MMR) — not estimated, not the v1 baseline repeated, and worse
than v1's 100%. That is the intended outcome of rebuilding this set with
genuinely harder material, not a regression: a perfect score here would
mean the eval set is still too easy, the same conclusion the audit
reached about v1.

**Environment caveat, stated plainly:** no real embedding provider key
is configured in this environment, so the vector leg runs on
`LocalHashEmbeddingAdapter` — a real, honest, but non-semantic
fallback. Retrieval here leans heavily on the lexical (full-text search)
leg; a genuinely semantic embedding model would likely score
differently on paraphrase-heavy queries. This is disclosed, not
papered over — re-running this suite once a real provider key is
configured is a legitimate, worthwhile follow-up, not required reading
of these numbers as final.

### The single most important finding: refusal threshold, 0/10

Every one of the 10 `unanswerable_populated` queries scored a top fused
RRF score at or above the configured refusal threshold (`0.0082`,
`config.py`) — meaning Gate 1 would incorrectly let every single one of
these proceed to generation instead of refusing. Most landed on exactly
`1/61 ≈ 0.01639`, the RRF contribution of a single rank-1 hit in exactly
one leg — confirmed by hand, not a harness bug: with only 20 chunks in
the corpus, `websearch_to_tsquery` full-text search finds *some*
plausible lexical overlap for nearly any English query, and that alone
is enough to clear a threshold calibrated against a completely different
scenario (empty-KB rejection, where the top score is near zero because
there is nothing to match at all).

This is not a new problem — `retrieval-and-refusal.md`'s own Threshold
Calibration section (written for this corpus, describing the real
system) already states the threshold "has not yet been stress-tested
against queries that sound answerable but are not, asked against a
workspace whose knowledge base is genuinely populated." This run is the
first time that gap has actually been measured rather than described.
**Recalibrating the threshold is explicitly not done here** — Phase 6
(parameter sweep) is where RRF/MMR/threshold constants get evaluated
against real data; this phase's job was building the eval set that
makes the gap visible, not closing it.

### Near-miss: distractors win a third of the time

Of the near-miss queries where the ranking could be evaluated, the
plausible-but-wrong distractor chunk outranked the genuinely correct
one 33.3% of the time. `near-miss-01-outbox-retry-forever` is the
starkest example: the router's retry-before-first-token rule (a
different subsystem's differently-scoped policy) ranked first, while
the actually-correct chunk (the outbox's dead-letter behavior) didn't
appear in the top 10 at all.

## Known limitations (stated, not hidden)

- **Faithfulness is not measured**, same as v1 and for the same reason:
  no real LLM provider key is configured in this environment. This
  phase built no chat-turn faithfulness machinery at all — that's
  explicitly Phase 7's job (real-provider run), not this one's.
- **A real markdown-parsing bug was found and worked around, not
  fixed**: `apps/api/src/aether/app/ingestion/parsers/markdown_parser.py`'s
  `_plain_text` joins CommonMark inline-token content with `""`, which
  silently drops the space a `softbreak` token represents — any
  markdown paragraph manually hard-wrapped across multiple source lines
  loses the space at each wrap point after extraction (e.g. "short
  lifetime" becomes "shortlifetime"). v1's corpus never triggered this
  (every paragraph there fits on one physical line); v2's corpus
  documents were reflowed to single-line-per-paragraph specifically to
  route around it. The bug itself is unfixed and unreported anywhere
  except here and the final session report — fixing `app/ingestion`
  code is out of scope for an eval-set-building phase.
- **This golden set does not gate CI.** Unlike v1 (which gates on a
  verified 100% baseline), there is no established "should be X%"
  target for v2 yet — wiring it into `eval-smoke`/`eval-nightly` as an
  actual regression gate is a deliberate follow-up decision, not
  assumed here.
- **RRF/MMR/chunking constants are unevaluated against this data** —
  this phase measured against the *current* constants; tuning them is
  Phase 6's explicit scope.
