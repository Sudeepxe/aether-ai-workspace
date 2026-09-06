# RAG and PR #135 Audit Report

**Scope:** Read-only audit of the repository at branch `groq-llm-integration`
(PR #135), including the RAG pipeline and the Groq provider integration.

**Verification performed:** Focused Groq/router/settings tests passed: **44/44**.
No repository files were modified as part of the audit.

## A. Verdict

This is not a tutorial-shaped repo: it has real separation of ingestion,
retrieval, generation, metrics, CI, operational drills, and a reproducible
mechanical eval harness. The commit history also shows iterative work across
24 days, rather than a single dump.

The strongest interview risk is a gap between security design prose and
implementation. Retrieved document text is interpolated directly into the
system prompt without a data boundary or adversarial validation. The docs
correctly admit that real-provider adversarial safety and faithfulness are
unmeasured; PR #135 then introduces another real provider without closing
either gap.

## B. Findings

| # | Severity | Area | Finding | Evidence |
|---|---|---|---|---|
| 1 | CRITICAL | Prompt injection | Retrieved documents are raw text inside the system prompt. An injected `IGNORE PREVIOUS INSTRUCTIONS` payload has no delimiter, escaping, or structural separation, so exposure is fully exposed for real LLMs. | `apps/api/src/aether/app/llm/router.py:45-51,206-215` |
| 2 | HIGH | Evaluation | The 20-case set is explicitly below the planned ~150 cases; its documents collapse to one chunk, so its retrieval hit metric does not prove chunk-level recall or ranking quality. | `evals/golden/v1/README.md:3-7,30-36`; `evals/harness/metrics.py:69-78` |
| 3 | HIGH | Groundedness | Faithfulness and adversarial safety against a real model remain unmeasured. The reported 100% results are mechanical metrics; that distinction is honest, but it remains a release-blocking evidence gap. | `docs/evals/latest-report.md:9-31` |
| 4 | HIGH | PR #135 / cost control | Groq accepts any configured model but applies one fixed context window and price pair. The default model's values are also wrong: current Groq pricing implies 7,500 input and 30,000 output microcents/1K, versus 5,900 and 7,900 configured. | `apps/api/src/aether/adapters/groq/completion.py:29-62`; official source: <https://console.groq.com/docs/model/openai/gpt-oss-20b> |
| 5 | HIGH | Refusal evaluation | All unanswerable golden cases use an empty KB; the repo documents no populated-KB, off-topic negative case in the golden suite. That cannot establish the real operating point of the refusal threshold. | `evals/golden/v1/README.md:16-28`; `docs/evals/latest-report.md:31-34` |
| 6 | MEDIUM | Retrieval tuning | RRF, MMR, candidate limits, and prompt `k` are defaults rather than evaluated choices. The source still states the golden set did not exist when those constants were selected. | `apps/api/src/aether/app/retrieval/hybrid_search.py:6-13,25-30` |
| 7 | MEDIUM | Retrieval resilience | The vector retrieval leg catches every exception and silently degrades to lexical retrieval. Programmer errors and data-contract failures are therefore indistinguishable from a genuine vector-index outage. | `apps/api/src/aether/app/retrieval/hybrid_search.py:80-90` |
| 8 | MEDIUM | CI coverage | PR #135 adds Groq and shared OpenAI-compatible code, but the eval-smoke path filter does not include either new directory. Future changes there can skip the RAG regression suite. | `.github/workflows/ci.yml:301-305` |
| 9 | MEDIUM | Git history | The history shows substantial implementation bursts, including 71-89-file scaffold commits and the current 18-file/809-line PR. It looks iterative afterward, but this remains an interview question about authorship and reviewability. | `git log --stat`; PR commit `805b8e8` |
| 10 | LOW | Production validation | The new Groq adapter has strong mocked wire tests, but no authenticated provider smoke test. A real provider-specific streaming/schema change would only surface after deployment. | `apps/api/tests/unit/test_groq_adapter.py:43-146` |

## C. Interview Questions

1. **How do you prevent indirect prompt injection from an uploaded document?**
   - Weak answer: “The system prompt tells the model not to follow it.”
   - Repo currently supports: raw retrieved text is placed in the system prompt.

2. **What real-model evidence proves your injection defense works?**
   - Weak answer: “The adversarial tests are green.”
   - Repo currently supports: adversarial safety is explicitly unmeasured.

3. **Why is retrieval hit-rate 100%?**
   - Weak answer: “The retriever is excellent.”
   - Repo currently supports: single-chunk synthetic documents and document-level matching.

4. **How did you calibrate refusal against off-topic queries in populated workspaces?**
   - Weak answer: “The threshold came from the eval set.”
   - Repo currently supports: golden negatives use empty KBs.

5. **Why these RRF/MMR/top-k values?**
   - Weak answer: “They are standard defaults.”
   - Repo currently supports: defaults are documented, not tuned.

6. **How would you detect an HNSW/vector outage versus an application bug?**
   - Weak answer: “We fall back to lexical.”
   - Repo currently supports: all vector exceptions degrade to lexical.

7. **How is citation precision different from answer faithfulness here?**
   - Weak answer: “Citations prove the answer.”
   - Repo currently supports: faithfulness remains unmeasured.

8. **Why does Groq have those billing constants?**
   - Weak answer: “They are approximate.”
   - Repo currently supports: they understate the default model's current pricing.

9. **What happens if `AETHER_GROQ_MODEL` changes to a different model?**
   - Weak answer: “The adapter is generic.”
   - Repo currently supports: capabilities, cost, and context stay fixed.

10. **How is the Groq integration regression-tested in CI?**
    - Weak answer: “The eval path covers RAG.”
    - Repo currently supports: its source paths are missing from eval-smoke filtering.

11. **Why is the generation gate trustworthy if no real model has been judged?**
    - Weak answer: “The prompt is strict.”
    - Repo currently supports: it is unvalidated against real providers.

12. **How do citations remain valid after document deletion?**
    - Weak answer: “They point to the document.”
    - Repo currently supports: a persisted citation model and deletion-aware design.

13. **What retrieval metric would reveal rank degradation at k=6?**
    - Weak answer: “Hit-rate.”
    - Repo currently supports: no recall@k, MRR, or NDCG over multi-chunk relevance.

14. **How would you choose a different embedding model?**
    - Weak answer: “Use the cheaper or better-known one.”
    - Repo currently supports: structure and versioning exist; comparative domain evidence does not.

15. **What operational proof exists beyond architecture documents?**
    - Weak answer: “We have dashboards.”
    - Repo currently supports: metrics, k6 evidence, restore drills, and some documented remaining gaps.

## D. Genuine Strengths

- Structure-aware chunking has explicit target, maximum, overlap, section
  boundaries, and provenance metadata:
  `apps/api/src/aether/app/ingestion/chunking.py:1-10,25-28`.
- Hybrid vector-plus-lexical retrieval, RRF, MMR, and a retrieval refusal gate
  are implemented rather than merely described:
  `apps/api/src/aether/app/retrieval/hybrid_search.py:59-101` and
  `apps/api/src/aether/app/retrieval/refusal_gate.py:1-33`.
- Gate-1 refusal avoids a generation call and records zero cost/citations:
  `apps/api/src/aether/app/chat/send_message.py:209-248`.
- Metrics include request latency, provider TTFT, ingestion duration, fallback,
  and settled cost: `apps/api/src/aether/observability/metrics.py:26-100`.
- The README states important limitations instead of inventing success claims:
  `README.md:121-148`.

## E. Prioritised Fix List

1. **Add delimited and escaped retrieved-context envelopes plus real-provider
   adversarial regression tests.** Estimated: 1-2 days. Closes findings 1 and 3.
2. **Correct Groq model metadata.** Either whitelist supported models with
   model-specific price/capability metadata or remove arbitrary model
   configuration. Estimated: 2-4 hours. Closes finding 4.
3. **Add multi-chunk corpora with chunk-level relevance labels, recall@k,
   MRR/NDCG, and populated-KB negatives.** Estimated: 2-4 days. Closes findings
   2 and 5.
4. **Run and retain real cross-family faithfulness and injection evaluations.**
   Estimated: 1 day plus credential governance. Closes finding 3.
5. **Include `adapters/groq` and `adapters/openai_compatible` in eval-smoke
   path filtering.** Estimated: under 1 hour. Closes finding 8.
6. **Narrow vector-search exception handling to expected infrastructure
   failures, and emit a degradation metric/log.** Estimated: 2-4 hours. Closes
   finding 7.
7. **Sweep RRF/MMR/k/chunking values against the expanded golden set and commit
   the result.** Estimated: 1-2 days. Closes finding 6.
8. **Add an opt-in authenticated Groq smoke test with CI-provided secrets.**
   Estimated: 2-4 hours. Closes finding 10.

## F. Missing Entirely

- Chunk-level relevance labels and ranking metrics such as recall@k, MRR, and
  NDCG.
- Real-model adversarial prompt-injection results.
- Real cross-family faithfulness results and human-calibrated judge agreement.
- Evidence-based comparison of embedding models and retrieval/chunking
  parameters on this corpus.
- Model-specific capability and cost metadata for configurable Groq models.

