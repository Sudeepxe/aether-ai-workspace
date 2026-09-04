# Phase 6 results — parameter sweeps against the v2 golden set

Real, measured results from real runs against `evals/corpora/v2`/
`evals/golden/v2/queries.json`, in this environment (`local-hash-fallback`
embedder, `embedding_version=1` — see the constraint note below before
reading anything in priority 4). Every sweep script monkey-patches the
real production module constants for the duration of one in-process run
and restores them immediately after — **no production constant in
`chunking.py` or `hybrid_search.py` was changed by running these
scripts**; confirmed by re-reading both files after every run in this
session.

**Reproduce:**
```
cd apps/api
PYTHONPATH=../.. uv run python -m evals.harness.threshold_sweep
PYTHONPATH=../.. uv run python -m evals.harness.chunking_sweep
PYTHONPATH=../.. uv run python -m evals.harness.retrieval_param_sweep
```
(requires `make dev` running, migrated, bucket provisioned — same
prerequisites as `retrieval_cli.py`)

## Constraint stated plainly, once, up front

Every number below was produced by `LocalHashEmbeddingAdapter` — a
real, deterministic, but **non-semantic** embedder (hash expansion of a
text's own bytes). This was a deliberate decision this session: no
local sentence-transformers adapter was added (a `vector(1536)`-fixed
DB column vs. all-MiniLM-L6-v2's 384 dimensions, and ADR-8.4 gates that
migration on eval recall verification existing first — which is what
this phase *is*), and no OpenAI key was configured. Priorities 1–3
below are largely embedder-independent (the lexical leg's real
full-text search ranking doesn't care what the vector leg's numbers
mean). **Priority 4 is not** — read its own section before drawing any
conclusion from it.

---

## Priority 1 — Refusal threshold recalibration

**Real precision/recall trade-off, every distinct observed fused
score, 53 queries (43 positive = answerable+near_miss, 10 negative =
unanswerable_populated):**

| threshold | TP | FP | TN | FN | precision | recall | specificity | F1 |
|---|---|---|---|---|---|---|---|---|
| 0.016393 *(current default is below this — everything passes)* | 43 | 10 | 0 | 0 | 81.1% | 100.0% | 0.0% | 89.6% |
| 0.028893 | 18 | 1 | 9 | 25 | 94.7% | 41.9% | 90.0% | 58.1% |
| 0.029052 | 17 | 1 | 9 | 26 | 94.4% | 39.5% | 90.0% | 55.7% |
| 0.029214 | 16 | 0 | 10 | 27 | 100.0% | 37.2% | 100.0% | 54.2% |
| 0.029551 | 15 | 0 | 10 | 28 | 100.0% | 34.9% | 100.0% | 51.7% |
| 0.029907 | 14 | 0 | 10 | 29 | 100.0% | 32.6% | 100.0% | 49.1% |
| 0.030214 → 0.032787 | 12→1 | 0 | 10 | 31→42 | 100.0% | 27.9%→2.3% | 100.0% | 43.6%→4.5% |
| 0.032788 | 0 | 0 | 10 | 43 | n/a | 0.0% | 100.0% | n/a |

**The current default (`0.0082`) sits below every real observed score**
(minimum observed: `0.016393`), which is exactly the 0/10 refusal-
correctness Phase 5 measured — every query, answerable or not, clears
it.

**The cost of fixing it, specifically on the 35 answerable queries (as
asked):** 19 of 35 (54%) score *exactly* `0.016393` — the same floor
value nearly every unanswerable query also lands on (it's `1/61`, one
leg's single rank-1 lexical hit — see Phase 5's README for why). **Any
threshold above the floor that correctly refuses the 10 unanswerable
queries also incorrectly refuses at least 25 of the 43 real positive
queries** — recall collapses to 37–42% at the first points where
specificity reaches ~90–100%.

**Recommendation: do not raise the threshold.** I did not change
`config.py`'s `retrieval_refusal_threshold`. The reasoning: the two
classes' score distributions overlap almost completely at this corpus
scale with this embedder — there is no single global cutoff that
meaningfully separates them without breaking more real, working
queries than it fixes. Best available "fix" (threshold ≈ 0.0292):
0/10 → 10/10 unanswerable correctness, at a cost of 27/43 (63%)
positive queries wrongly refused. That is not a trade worth making
silently, and it is not what "recalibration" should mean here. The
real fix for Gate 1 needs either a genuinely semantic embedder (this
session's explicit decision was not to add one) or a materially
different signal than a single raw RRF score threshold — a scope
larger than "recalibrate a constant." Gate 2 (the LLM's own instruction
to refuse when its context doesn't answer the question) remains the
actual backstop for the unanswerable-populated case in the meantime —
untouched and unaffected by anything in this phase.

---

## Priority 2 — Chunking parameters (embedder-independent on the lexical leg)

4 configurations, each a fresh real ingestion (chunk boundaries
genuinely differ per config; golden-set anchors resolved fresh against
each, zero unresolved anchors in every config):

| config | total chunks | recall@5 | recall@10 | MRR | NDCG@10 | near-miss recall@10 | distractor beats truth |
|---|---|---|---|---|---|---|---|
| baseline (512/800/12.5%) | 20 | 59.5% | 66.2% | 56.6% | 57.2% | 62.5% | 33.3% |
| smaller (256/400/12.5%) | 37 | 41.4% | 47.1% | 32.5% | 34.3% | 37.5% | 50.0% |
| **larger (768/1000/12.5%)** | **14** | **62.9%** | **90.0%** | **63.6%** | **66.9%** | **75.0%** | 42.9% |
| baseline size, minimal overlap (512/800/2%) | 20 | 55.7% | 60.0% | 49.7% | 52.0% | 68.8% | 14.3% |

**Real, meaningful differences — not noise.** Smaller chunks are
markedly worse on every metric (recall@10 drops 19 points). Larger
chunks are markedly better (recall@10 up 24 points to 90.0%). Overlap
matters too: cutting it from 12.5% to 2% costs 6 points of recall@10
and 7 points of MRR (though, interestingly, it *improves* the
near-miss distractor rate — a real, mixed result, not cherry-picked).

**Recommendation, with an honest caveat, not applied:** larger chunks
look better here, but this corpus is 6 documents / 14–37 chunks —
close enough to "one chunk per document" at the larger setting that
some of this improvement is a small-corpus artifact (less to
distinguish between, not necessarily a universally better chunking
strategy). **I did not change `chunking.py`'s constants.** This result
is worth retesting once the golden set is larger (closer to the
blueprint's ~150-case target) before treating it as production
guidance — a real, meaningful signal, but not yet a safe generalization
at this sample size.

---

## Priority 3 — k and candidate limits

Same ingested baseline corpus (20 chunks) for every row — only
query-time parameters vary:

| config | recall@5 | recall@10 | MRR | NDCG@10 | near-miss recall@10 |
|---|---|---|---|---|---|
| baseline (limits=20, k=10) | 59.5% | 66.2% | 56.6% | 57.2% | 62.5% |
| tight limits (5/5/5, k=5) | 59.5% | 59.5%* | 38.1% | 41.7% | 31.2% |
| medium limits (10/10/10, k=10) | 59.5% | 66.2% | **43.1%** | 47.1% | 62.5% |
| wider k (limits=20, k=15) | 59.5% | 66.2% | 57.3% | 57.2% | 62.5% |

\* recall@10 == recall@5 here because k=5 caps the metric — there's
nothing past rank 5 to find.

**Real, non-obvious finding:** recall@5 and recall@10 (whether the
right chunk appears *anywhere* in the top-k) barely move — but **MRR
drops sharply** (56.6% → 43.1%) just from shrinking MMR's own candidate
pool to 10, even though the vector/lexical leg limits are already ≥ the
corpus size and recall@10 is unaffected. Smaller MMR candidate pools
change *where* MMR ranks a relevant chunk, not just whether it survives
at all — a genuine ranking-quality cost that a recall-only metric would
have missed entirely. Widening k to 15 doesn't hurt and marginally
helps MRR (57.3% vs 56.6%) — no meaningful downside found to a wider k
at this corpus size.

**Not applied** — this corpus's total chunk count (20) already sits at
the current leg-limit default, so "candidate limits" isn't really being
tested at production scale here either; flagging the same small-corpus
caveat as priority 2.

---

## Priority 4 — RRF constant and MMR lambda

**Explicitly labeled, per the instruction: measured under a
non-semantic dense leg. Not provider-representative.**

| config | recall@5 | recall@10 | MRR | NDCG@10 | near-miss recall@10 |
|---|---|---|---|---|---|
| baseline (RRF=60, λ=0.5) | 59.5% | 66.2% | 56.6% | 57.2% | 62.5% |
| RRF=10 | 59.5% | 66.2% | 56.6% | 57.2% | 62.5% |
| RRF=100 | 59.5% | 66.2% | 56.6% | 57.2% | 62.5% |
| MMR λ=1.0 (pure relevance) | 59.5% | 66.2% | 56.6% | 57.2% | 62.5% |
| MMR λ=0.0 (pure diversity) | 55.2% | 70.0% | 53.0% | 55.2% | 50.0% |

**This is a null result for RRF constant (10 vs 60 vs 100: zero
difference on every metric) and for MMR λ between 0.5 and 1.0 (zero
difference) — stated plainly, not tuned away.** I am not presenting
this as "these constants don't matter" in general. The honest
explanation: with a non-semantic embedder, the vector leg's cosine
scores carry no real relevance signal, so its contribution to both RRF
fusion and MMR's similarity-based diversity penalty is close to noise
relative to the lexical leg's real term-match ranking — changing how
strongly RRF/MMR weight that noisy signal doesn't change which chunks
end up on top, because the noise was never driving the outcome in the
first place. λ=0.0 (pure diversity, explicitly *ignoring* relevance
entirely) is the one point that differs — and it's worse on recall@5
and MRR, better on recall@10, a genuinely mixed result, not a clean
win.

**What would need to change for these results to be trustworthy:** a
real semantic embedding model. Under a real embedder, the vector leg's
cosine similarity would carry actual relevance signal, RRF's constant
would genuinely trade off how much to trust rank position from a
meaningful leg, and MMR's diversity penalty would be operating on
real semantic similarity rather than noise. Until then, tuning these
two constants against this specific setup would be tuning against an
artifact of the embedder, not the retrieval architecture — not done
here, and not recommended before that constraint changes.

---

## Summary — what was and wasn't changed

| Item | Sweep result | Production constant changed? |
|---|---|---|
| Refusal threshold | Real fix breaks 63% of real answerable queries | **No** — current `0.0082` kept |
| Chunking (target/max/overlap) | Larger chunks meaningfully better, smaller worse | **No** — flagged as needing a bigger golden set first |
| Candidate limits / k | MMR pool size affects MRR even when recall doesn't | **No** — same small-corpus caveat |
| RRF constant / MMR lambda | Null result (10/60/100 identical; λ 0.5/1.0 identical) | **No** — explicitly not trustworthy under this embedder |

Nothing in `apps/api/src/aether/app/ingestion/chunking.py` or
`apps/api/src/aether/app/retrieval/hybrid_search.py` was edited this
phase. Every number above came from monkey-patching those modules'
constants in-process for one sweep script's run, then restoring them —
verified by re-reading both files after every run.
