# Remediation summary

Final deliverable of `docs/REMEDIATION_PLAN.md` (Phase 8's own required
output). Covers all 8 phases plus the exfiltration mitigation completed
just before Phase 8.

## Each audit finding: closed, partially closed, or deliberately not addressed

| # | Original finding | Status | Why |
|---|---|---|---|
| Phase 1 | Eval-smoke CI path filter missed provider directories | **Closed** | `adapters/groq`/`adapters/openai_compatible` added to the filter; full path-filter list audited against the current source tree. |
| Phase 2 | Vector retrieval swallows every exception, indistinguishable from real outages | **Closed** | `hybrid_search.py`'s except clause narrowed to real infrastructure exceptions; programming errors now propagate; a degradation counter + WARN log added; a test asserts an unexpected exception type surfaces rather than degrading silently. |
| Phase 3 | Retrieved context interpolated into the system prompt with no boundary — the critical finding | **Closed** | Retrieved content moved out of the system prompt entirely, into a delimited envelope in the final user message; delimiter-lookalike sequences inside chunk text are neutralized; instruction hierarchy stated explicitly as belt-and-braces, not the primary defense. 6 adversarial unit fixtures added. |
| Phase 4 | Groq adapter applies one fixed price/context pair regardless of configured model | **Closed** | Replaced with a per-model metadata table (`_MODEL_METADATA`); an unrecognized `AETHER_GROQ_MODEL` now fails loudly at startup instead of silently reusing another model's pricing; the source comment records the verification date (2026-09-04) so staleness is visible next time. |
| Phase 5 | Golden set is document-level, not chunk-level; every unanswerable case uses an empty KB | **Closed for the eval-set/harness engineering itself; partially closed on the metric it was built to enable** | A real multi-chunk v2 corpus (6 documents, chunk-level relevance labels, populated-KB negatives) replaced v1; recall@k/MRR/NDCG@k implemented over chunk-level relevance; a faithfulness/groundedness check was implemented and runs on every reply. **Partial:** faithfulness has never returned an actual measured score in this environment — it correctly returns `not_measured` every time, by design (ADR-6.5, no second provider family configured), not because the check is broken. |
| Phase 6 | RRF/MMR/candidate-limit/chunking constants are unevaluated library defaults | **Closed on evaluation; deliberately not closed on adoption** | Every named parameter was really swept against the v2 set and the full results table (including rejected options) is committed (`evals/golden/v2/PHASE6_RESULTS.md`). **Deliberately not adopted:** even where a sweep found a meaningful difference (larger chunking: 66.2%→90.0% recall@10), the production constant was left unchanged because the corpus (14 chunks at that setting) is too small to trust the result as generalizable — a small-corpus-artifact risk stated plainly, not silently accepted. The RRF/MMR-λ sweep is a genuine null result under the current non-semantic embedder, also left unchanged, with the mechanism explained (ADR-6.3's Phase 6 update). The plan's optional "compare two embedding models" item was **not attempted** — no second embedder exists in this environment (deliberate, ADR-8.4). |
| Phase 7 | No real model has ever been evaluated; all adversarial/faithfulness results were mechanical | **Partially closed** | Real adversarial (6 fixtures) and real faithfulness runs were executed against a real model (`openai/gpt-oss-20b`, Groq) — a genuine, reproduced failure was found (system-prompt exfiltration, 3/4 real trials) and reported as a failure, not hidden. **Not closed as asked:** the plan specifically requested "at least two real providers from different families" — only one provider family (Groq) was ever configured in this environment, so no cross-family comparison exists. Faithfulness is real-generation-backed but still `not_measured` for the same reason. |
| Exfiltration mitigation (this session, pre-Phase-8) | Real exfiltration vulnerability found in Phase 7 | **Mitigated, not fixed — stated as such deliberately** | Two independent layers (non-disclosure clause + output-side verbatim-span detector) reduced a 3-of-4 real leak rate to 0 of 6 full leaks in a real re-test — see `evals/golden/v2/PHASE7_MITIGATION_RESULTS.md`. A real residual risk remains (a short prefix can still reach the caller; paraphrase-style attacks untested) and is stated as such, not glossed over. |
| Phase 8 | Documentation and trade-offs | **Closed by this document and its companions** | ADRs updated with real Phase 6/7 citations (ADR-6.3, ADR-6.4); the deliberate "no authenticated provider smoke test in CI" trade-off documented (`docs/TRADE_OFFS.md`); README updated with real cost/index-lifecycle/scale-ceiling data and an expanded limitations section; a 3-item real failure log written (`docs/FAILURE_LOG.md`); this summary. |

## Every number that changed, old → new, and how it was measured

| Metric | Old | New | How measured |
|---|---|---|---|
| Groq `openai/gpt-oss-20b` pricing (microcents/1K prompt, completion) | 5,900 / 7,900 (stale, actually Llama 3.3 70B Versatile's rate) | **7,500 / 30,000** | Cross-checked against Groq's published model docs, 2026-09-04 (`adapters/groq/completion.py`). |
| Retrieval hit-rate (v1 golden set) | 100% (document-level; every relevant document collapsed to one chunk) | **Real chunk-level recall@5 = 59.5%, recall@10 = 66.2%, MRR = 56.6%, NDCG@10 = 57.2%** (v2, baseline chunking) | Real ingestion of the v2 multi-chunk corpus, real `HybridSearch` queries against `LocalHashEmbeddingAdapter`, chunk-level relevance labels (Phase 5), `evals/harness/retrieval_param_sweep`/Phase 6. |
| Refusal correctness on populated-KB negatives | Never measured (v1 used only an empty KB) | **0/10 at the current threshold (0.0082); best alternative threshold (≈0.0292) reaches 10/10 unanswerable-correct at the cost of wrongly refusing 27/43 (63%) real answerable queries** | Real precision/recall sweep across every distinct observed fused score, 53 real queries (Phase 6, `evals/golden/v2/PHASE6_RESULTS.md`, Priority 1). |
| Adversarial safety (real model) | Not applicable — only `EchoGenerator` existed (mechanical eval only) | **5/6 fixtures held; 1/6 (exfiltration) failed 3 of 4 real trials → after mitigation, 0 of 6 full leaks in a real re-test** | Real Groq calls through the real message-assembly and (post-mitigation) the real `LlmRouter.generate()` (Phase 7 RUN 1; this phase's `exfiltration_mitigation_live_run.py`). |
| Faithfulness (35 real answerable-query replies) | Never computed | **35/35 `not_measured`** (correct behavior for a single-provider environment, not a result) | Real `judge_faithfulness()` calls against real Groq replies (Phase 7 RUN 2). |
| Cost per 1000 queries | Undocumented | **≈$0.106** (`openai/gpt-oss-20b`) | Phase 4's verified pricing × real observed average token usage from Phase 7's live runs (≈405 prompt tokens, n=6; ≈251 completion tokens, n=38) — see README's new Cost section for the full derivation and its stated caveat (single-chunk prompts, likely an underestimate of a real 6-chunk production turn). |
| RRF constant (10 / 60 / 100) and MMR λ (0.5 / 1.0) | Unevaluated library defaults | **Confirmed null result — identical metrics across every value tested**, with the non-semantic-embedder mechanism explained, not just observed | Real sweep, `evals/harness/retrieval_param_sweep` (Phase 6, Priority 4). Constants left unchanged. |
| New observability surface | — | **`aether_llm_system_prompt_leak_blocked_total`** counter added | New metric (`observability/metrics.py`), this session — not a changed number, a new one. |

## Things found that the original audit missed

- **A real markdown-parsing bug**: `markdown_parser.py`'s `_plain_text` drops
  the space at every manually hard-wrapped line break inside a paragraph
  (a CommonMark `softbreak` token joined with `""`). Found while building the
  Phase 5 corpus, worked around (not fixed — out of that phase's scope), and
  still live in `apps/api/src/aether/app/ingestion` today (`docs/FAILURE_LOG.md`).
- **`openai/gpt-oss-20b` is a reasoning model that can silently exhaust its
  entire token budget on hidden chain-of-thought**, returning a visibly empty
  reply at the real production default (1,024 tokens) — confirmed by
  inspecting the raw SSE body directly. The audit anticipated generic
  provider failures; it did not anticipate this specific, model-family-level
  quirk, which both Phase 7 scripts and this phase's mitigation re-test had
  to explicitly detect and retry around.
- **A daily token quota, separate from and harder than the per-minute rate
  limit**, with no useful backoff (retry-after values of 10-40+ minutes) —
  hit mid-run during Phase 7, causing 7 of 10 `unanswerable_populated`
  queries to be honestly reported as "could not run" rather than silently
  dropped. The audit's "keep credentials out of the repo" instruction did not
  anticipate this operational failure mode.
- **The target model is materially non-deterministic on identical adversarial
  input** — the exfiltration fixture leaked in 3 of 4 trials, not 4 of 4 or
  1 of 4, run with identical code, fixture, and config. This has a real
  consequence for how any single-run "it held" or "it failed" claim about
  this model should be read going forward — a claim needs a trial count
  attached to be honest, not just a verdict.
- **The refusal-threshold problem is worse than "needs recalibration."** The
  original audit and this project's own prior ADR (6.4) both framed the
  threshold as a calibrated-but-fixed constant that would need updating.
  Phase 6's real sweep found something stronger: at this corpus scale and
  embedder, **no single global threshold separates the two classes at all** —
  recalibration on its own cannot close this gap; a materially different
  signal is needed.

## Things unable to be verified, stated plainly

- **Per-stage latency (embed → retrieve → generate)** — never benchmarked in
  this environment. `TBD — not yet measured` in the README, not a fabricated
  split of an end-to-end number.
- **Real cross-family faithfulness** — never computed; every real generation
  in Phase 7 correctly reported `not_measured` because only one provider
  family (Groq) was ever configured.
- **7 of 10 `unanswerable_populated` queries in Phase 7's RUN 2** — never
  actually run at all (Groq's daily quota was exhausted mid-session).
- **Whether Phase 6's larger-chunking recall improvement (66.2%→90.0%)
  generalizes past this specific 6-document/14-chunk corpus** — explicitly
  flagged as a small-corpus-artifact risk, not resolved either way.
- **Whether the exfiltration mitigation holds against a paraphrase or
  translation-style attack** (one that never reproduces 50 contiguous
  verbatim characters of the system prompt) — not tested; the detector is
  specifically a verbatim-span check, and this is a known, stated gap in
  what it covers.
- **Whether a second real provider family would show the same exfiltration
  behavior, rate, or respond the same way to the mitigation** — untested;
  every real-provider number in this engagement is Groq/`openai/gpt-oss-20b`
  only.

## Three questions an interviewer is now most likely to ask

**1. "You found a real prompt-injection exfiltration vulnerability — is it
fixed now?"**
Honest answer: mitigated, not fixed, and I'd say that in exactly those
words. Two independent layers cut a measured 3-of-4 real leak rate down to
0 of 6 full leaks in a real re-test through the actual production code
path. But the design has a real, disclosed gap: because Aether streams
live over SSE, a short prefix of the prompt (confirmed up to ~50 characters
in 4 of those 6 re-test trials) reaches the client before detection fires
— a live stream can't un-send tokens. I'd also point out the leaked
content itself was low-sensitivity (a generic grounding instruction, no
credentials or business logic) — the real finding is that the model's own
instruction-following boundary is unreliable under this attack shape, which
matters more for what a future, more sensitive system prompt might contain
than for what leaked this time.

**2. "Your retrieval numbers — 66% recall@10 — aren't very high for a RAG
system. Why?"**
Honest answer: those numbers are real and measured under a deliberate
constraint stated up front, not a hidden weakness — the only embedder
configured in this environment is a non-semantic hash expansion, not a
trained embedding model (adding a real one is gated behind ADR-8.4's own
recall-verification requirement, which is exactly the work that produced
this number). I'd frame 66.2% as a real, honest lower bound, not the
system's ceiling, and point to Phase 6's finding that larger chunking
measured 90.0% at this same corpus — with the immediate, unprompted caveat
that the corpus is small enough (14 chunks) that I don't yet trust that
number to generalize, which is itself evidence the process is working
rather than cherry-picking a good-looking number.

**3. "Why didn't you just raise the refusal threshold since it's not
catching unanswerable queries?"**
Honest answer: because I measured the trade-off first instead of assuming
one existed. A real sweep across every observed score showed there is no
single global threshold that separates answerable from unanswerable
queries at this corpus scale — the best available alternative fixes 10/10
unanswerable detection but wrongly refuses 63% of real, working answerable
queries. That's not a fix, it's trading one measured failure for a larger
one, so I left the constant alone and documented why. I'd also point out
that Gate 2 (the model's own generation-time refusal instruction) is
already doing real, confirmed backstop work here — every real "false
refusal" Phase 7 observed traced back to an actual retrieval miss, not a
generation failure, so the exposure this leaves is narrower than the raw
threshold number alone would suggest.
