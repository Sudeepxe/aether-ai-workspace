## A. Verdict

The remediation closed several concrete engineering gaps: Groq metadata is now model-specific, vector failures are narrowed, the retrieval eval is now chunk-level, and retrieved documents no longer enter the system prompt directly. Those claims are supported by code.

But this still reads as an evidence-rich portfolio system rather than a production RAG system. The most damaging issue is new: user-controlled conversation content is compacted, stored, and later appended to the system prompt as "Earlier conversation summary." That reintroduces an instruction-privilege escalation path immediately beside the remediation that claims the system prompt is own-text-only.

## B. Findings table

| # | Severity | Area | Finding | Evidence |
|---|---|---|---|---|
| 1 | CRITICAL | Prompt injection / memory | Conversation summaries are treated as trusted system text even though they are derived from user messages and prior model output. A malicious earlier turn can persist instructions into later system prompts. The remediation comment's claim that summaries are "not attacker-influenced" is false. | `apps/api/src/aether/app/chat/memory_assembly.py:71-104`; `apps/api/src/aether/adapters/llm/memory_compaction.py:36-47`; `apps/api/src/aether/app/llm/router.py:372-380` |
| 2 | HIGH | Abstention | The configured retrieval threshold remains 0.0082 despite the v2 evaluation recording 0/10 correct refusals for populated-KB unanswerable queries. Gate 1 therefore lets all tested negatives reach generation. | `apps/api/src/aether/config.py:130-149`; `apps/api/src/aether/app/retrieval/refusal_gate.py:29-33`; `evals/golden/v2/README.md:109-128` |
| 3 | HIGH | Security mitigation regression | The output leak detector only stops output after accumulating 50 leaked characters; previous leaked tokens have already reached the SSE client. It then returns normally without provider usage, causing the paid provider call to persist and settle at zero cost. | `apps/api/src/aether/app/llm/router.py:305-336`; `apps/api/src/aether/app/chat/send_message.py:254-261`; `apps/api/src/aether/app/chat/send_message.py:336-361` |
| 4 | HIGH | Remediation-summary overstatement / CI | The summary says the eval-smoke path filter was fully audited, but it omits `embedding_selection.py`, which selects the embedder used by both ingestion and query retrieval. It also omits the Postgres retrieval adapter and retrieval port. | `docs/REMEDIATION_SUMMARY.md:11`; `.github/workflows/ci.yml:301-306`; `apps/api/src/aether/embedding_selection.py:31-43`; `apps/api/src/aether/adapters/postgres/chunk_search.py:62-131` |
| 5 | HIGH | Evaluation regression | The new v2 retrieval suite is not a CI gate. CI still runs only the old v1 suite, whose 100% document-level score was the original methodological weakness. A ranking regression can merge without failing CI. | `.github/workflows/ci.yml:276-288`; `.github/workflows/ci.yml:340-350`; `evals/golden/v2/README.md:162-166` |
| 6 | HIGH | README accuracy | The top-level README claims "measured faithfulness," while its own later status table and limitations state faithfulness is not determinable/not measured. This is a public-facing overclaim. | `README.md:5-7`; `README.md:124-126`; `README.md:272-279` |
| 7 | HIGH | Retrieval quality | All measured v2 retrieval numbers are from `LocalHashEmbeddingAdapter`, a non-semantic fallback. The project has no comparison to another embedding model, so current retrieval claims do not validate semantic retrieval. | `apps/api/src/aether/embedding_selection.py:31-43`; `evals/golden/v2/README.md:99-107`; `docs/TRADE_OFFS.md:68-76` |
| 8 | MEDIUM | Evaluation applicability | The v2 harness evaluates top 10 retrieved chunks, while production sends top 6 to generation. Recall@10 and NDCG@10 are useful diagnostics, but they are not the production prompt-context metric. | `evals/harness/retrieval_runner.py:27-31`; `apps/api/src/aether/app/retrieval/hybrid_search.py:30-35` |
| 9 | MEDIUM | Ingestion correctness | The Markdown parser concatenates inline token content with no separator. CommonMark soft line breaks can remove spaces and alter indexed content, corrupting chunk text and lexical retrieval. | `apps/api/src/aether/app/ingestion/parsers/markdown_parser.py:57-60` |
| 10 | MEDIUM | Prompt injection | Retrieved context is structurally improved—moved to a user message and enclosed—but injection exposure is only partial, not eliminated. The real-model mitigation records prior prompt exfiltration and acknowledges prefix leakage and untested paraphrase attacks. | `apps/api/src/aether/app/llm/router.py:64-125`; `apps/api/src/aether/app/llm/router.py:305-327`; `README.md:250-262` |
| 11 | MEDIUM | Documentation drift | Retrieval source comments still say the golden set did not exist and parameters were not evaluated, although Phase 5/6 added an eval and sweep. This makes code-level rationale stale. | `apps/api/src/aether/app/retrieval/hybrid_search.py:6-13`; `evals/golden/v2/PHASE6_RESULTS.md:1-20` |
| 12 | LOW | Real-provider validation | The real-provider assessment remains single-family Groq only; the judge intentionally returns `not_measured`, so no cross-family faithfulness score exists. | `docs/REMEDIATION_SUMMARY.md:17`; `evals/harness/faithfulness_live_run.py:16-29`; `README.md:272-279` |

**Verification run:** remediation-focused tests passed, 71 passed. Ruff formatting/lint and mypy also passed.

## C. The 15 questions I would ask in the interview

1. How does a malicious conversation turn avoid becoming a system instruction after memory compaction?
   - Weak answer: "Only retrieved documents are untrusted."
   - Repo currently supports: summaries of user/assistant messages are appended to the system prompt.

2. Why is Gate 1 still enabled at a threshold that refused 0/10 populated-KB negatives?
   - Weak answer: "Gate 2 is a backup."
   - Repo currently supports: the threshold is retained despite a measured failure.

3. How is a detected prompt leak billed and persisted?
   - Weak answer: "We block it."
   - Repo currently supports: the provider call can be paid but recorded as zero-cost.

4. Why does the remediation summary call the CI filter fully audited when it excludes the embedder-selection module?
   - Weak answer: "That module is just composition."
   - Repo currently supports: it chooses the active retrieval embedder.

5. What prevents a v2 ranking regression from merging?
   - Weak answer: "The eval suite runs in CI."
   - Repo currently supports: only v1 is CI-gated.

6. Is faithfulness measured?
   - Weak answer: "Yes, it says so in the README."
   - Repo currently supports: it is explicitly not measured cross-family.

7. Which real semantic embedding model beat the local hash fallback?
   - Weak answer: "OpenAI can be configured."
   - Repo currently supports: no comparative run.

8. Why should recall@10 describe production answers when production passes six chunks?
   - Weak answer: "More results is better."
   - Repo currently supports: evaluation and prompt context use different k values.

9. How does the Markdown parser preserve soft line breaks?
   - Weak answer: "markdown-it handles Markdown."
   - Repo currently supports: parser concatenation drops spaces.

10. Can a document still induce model behavior despite delimiters?
    - Weak answer: "It is in a user message now."
    - Repo currently supports: partial mitigation; real-model exfiltration occurred.

11. Why does the leak detector only catch verbatim prompt spans?
    - Weak answer: "Prompt leakage is solved."
    - Repo currently supports: paraphrase and translation attacks remain untested.

12. What does the 0/10 refusal result mean operationally?
    - Weak answer: "The corpus is small."
    - Repo currently supports: no trustworthy Gate-1 separator at the tested configuration.

13. How do you distinguish lexical fallback from a retrieval quality issue?
    - Weak answer: "We emit a counter."
    - Repo currently supports: expected vector failures are observable; quality degradation is not independently evaluated in production.

14. Why are RRF/MMR comments still describing an era before the eval set?
    - Weak answer: "The docs have the updated results."
    - Repo currently supports: source-level rationale is stale.

15. Which remediation claim should an interviewer distrust first?
    - Weak answer: "All closed items are verified."
    - Repo currently supports: the "full path-filter audit" claim is disproved by the filter itself.

## D. Genuine strengths

- Retrieved chunks no longer enter the system prompt and are wrapped in a distinct context envelope: `apps/api/src/aether/app/llm/router.py:64-125`, `apps/api/src/aether/app/llm/router.py:362-406`.
- Unknown Groq model identifiers fail at startup rather than silently applying incorrect costs: `apps/api/src/aether/adapters/groq/completion.py:61-93`.
- Vector-search degradation is now limited to explicit backend-unavailability errors and emits a log plus metric: `apps/api/src/aether/app/retrieval/hybrid_search.py:85-105`.
- The v2 eval runs against `HybridSearch` directly and implements chunk-level recall, MRR, and NDCG: `evals/harness/retrieval_runner.py:108-144`, `evals/harness/retrieval_metrics.py:79-142`.
- The repo documents major unresolved risks rather than hiding them: `README.md:231-281`.

## E. Prioritised fix list

1. Move memory summaries out of the system prompt; treat them as untrusted contextual data with the same envelope model as retrieval. Add adversarial persistence tests. 1 day. Closes #1.
2. Make leak intervention a distinct terminal security state, preserve authoritative provider usage when available, and reconcile/record aborted-call cost. 1 day. Closes #3.
3. Add `embedding_selection.py`, `adapters/postgres/chunk_search.py`, and `ports/retrieval.py` to the eval-smoke filter. 30 minutes. Closes #4.
4. Gate v2 retrieval regressions in CI with explicit, conservative baseline policy. 0.5–1 day. Closes #5.
5. Remove or correct the README's "measured faithfulness" claim. 15 minutes. Closes #6.
6. Replace the known-invalid global refusal threshold with a different signal or disable Gate 1 for populated-KB cases until one exists. 2–5 days. Closes #2.
7. Fix Markdown softbreak handling and add parser regression tests. 1–2 hours. Closes #9.
8. Report production-equivalent recall@6 alongside diagnostic recall@10, and include query rewriting where applicable. 0.5 day. Closes #8.
9. Run at least one real semantic embedding comparison on the v2 corpus. 1 day plus credential governance. Closes #7.
10. Update stale retrieval source comments to cite the v2 evidence and its limits. 1 hour. Closes #11.

## F. What is missing entirely

- A trustworthy calibrated abstention signal for populated knowledge bases.
- Cross-family faithfulness measurement.
- A semantic-embedding comparison on the project corpus.
- CI enforcement for the v2 ranking evaluation.
- A safe trust boundary for persisted conversation summaries.
- Reliable cost accounting for policy-aborted streamed generations.
