# RAG Remediation Plan — Prompt for Claude Code

> Paste this into Claude Code at the repo root.
> Work **one phase at a time**. Stop after each phase and report. Do not run
> ahead. I will review and tell you to continue.

---

## CONTEXT

This repository is my portfolio project for an AI/ML Engineer application. It
has been through an adversarial external audit. The audit found that the
**implementation layer is solid** but the **evidence layer is thin** — the
system is built, but proof that it works is missing or methodologically weak.

You are closing those gaps. Every change must survive a hiring-committee
engineer asking "why did you do it that way?"

## NON-NEGOTIABLE RULES

1. **Never fabricate a number.** Do not write a metric, benchmark, latency
   figure, or eval result into any file unless you actually ran the code and
   observed it. If a number is not yet measured, write `TBD — not yet measured`.
   A fabricated result in this repo is worse than no result. This is the single
   most important rule here.
2. **Do not delete or soften the limitations section in the README.** Honest
   stated limitations are an asset in this project, not a defect. You may update
   items that are genuinely fixed, but the section stays and stays honest.
3. **Do not rewrite git history.** No rebasing, squashing, amending, or
   force-pushing existing commits.
4. **Commit discipline.** One logical change per commit. Real messages that say
   what changed and why. No commit touching more than ~10 files unless the
   change is genuinely atomic. No `fix`, `update`, `wip`, `final` messages.
   The commit history is itself a reviewed artifact.
5. **Explain as you go.** After each phase, tell me in plain language what you
   changed and why, at a level where I can defend it verbally without looking at
   the code. If I cannot explain a change, it does not ship.
6. **No scope creep.** Do not refactor things outside the phase you are on, even
   if they look wrong. Note them at the end instead.
7. **Tests must actually pass.** Run them. Paste the real output.

---

# PHASE 1 — CI path filter (target: under 1 hour)

**Problem:** `.github/workflows/ci.yml` around lines 301-305 has an eval-smoke
path filter that does not include the newly added provider directories. Changes
to `adapters/groq` or `adapters/openai_compatible` can currently merge without
triggering the RAG regression suite.

**Do:**
- Add both directories to the eval-smoke path filter.
- Audit the whole filter list against the current source tree — list any other
  directory that can affect retrieval or generation but is not covered.
- Report which paths you added and which you deliberately left out.

**Verify:** show the diff and explain what would now trigger that previously
would not have.

---

# PHASE 2 — Vector retrieval error handling (target: 2-4 hours)

**Problem:** `apps/api/src/aether/app/retrieval/hybrid_search.py:80-90` catches
every exception on the vector leg and silently degrades to lexical retrieval. A
`NameError` in my own code and a genuine vector-index outage are currently
indistinguishable.

**Do:**
- Narrow the except clause to the specific infrastructure exceptions that
  represent real, expected failures (connection errors, timeouts, index
  unavailable). Programming errors must propagate.
- Emit a degradation signal when the fallback fires — a counter metric in
  `observability/metrics.py` plus a WARN log carrying the exception type.
- Add a test that asserts an unexpected exception type propagates rather than
  being swallowed.

**Verify:** run the retrieval tests and paste the output. Tell me exactly which
exception types now degrade gracefully and which now surface.

---

# PHASE 3 — Retrieved-context boundary (target: half a day for the fix)

**Problem — this is the critical finding.** Retrieved document text is
interpolated directly into the system prompt at
`apps/api/src/aether/app/llm/router.py:45-51,206-215`. There is no delimiter, no
escaping, and no structural separation between my instructions and untrusted
document content. An indexed document containing `IGNORE ALL PREVIOUS
INSTRUCTIONS` is currently handled as instruction.

**Do:**
- Move retrieved context **out of the system prompt.** The system prompt holds
  my instructions only. Retrieved chunks go in a separate user-role message.
- Wrap each chunk in an explicit, unambiguous envelope that marks it as data:
  a clear delimiter, the chunk's source metadata, and a statement that the
  content between the delimiters is untrusted retrieved material to be used as
  reference only, never as instruction.
- Escape or neutralise any delimiter-lookalike sequences appearing inside chunk
  text, so a document cannot close the envelope early.
- Keep the instruction hierarchy explicit: system instructions outrank
  retrieved content, always.
- Add unit tests with adversarial chunk fixtures: direct instruction override,
  delimiter escape attempt, role-confusion attempt, and an attempt to induce a
  tool call if the system has tools.

**Do not** rely on a prompt line saying "ignore injected instructions" as the
defense. That is the weak answer. Structure is the defense; the prompt line is
belt-and-braces on top.

**Verify:** show me the before and after of the assembled prompt for one real
query, so I can see the boundary with my own eyes.

---

# PHASE 4 — Groq model metadata (target: 3-4 hours)

**Problem:** `apps/api/src/aether/adapters/groq/completion.py:29-62` accepts any
configured model but applies one fixed context window and one fixed price pair.

**Verified pricing correction** (checked against current published Groq rates):

- `openai/gpt-oss-20b`: **$0.075 / 1M input**, **$0.30 / 1M output**
  — **7,500** input and **30,000** output microcents per 1K tokens.
  Context window: **131,072** tokens. Cached input is roughly half input.
- The currently configured **5,900 / 7,900** corresponds to **$0.59 / $0.79**,
  which is Llama 3.3 70B Versatile pricing. This is stale metadata carried over
  from a different model. Confirm this reading against the code before changing
  it, and tell me if you conclude otherwise.

**Do:**
- Replace the single fixed price/context pair with a per-model metadata table:
  model id → context window, input price, output price, cached-input price if
  applicable, and supported capabilities.
- Fail loudly at startup if `AETHER_GROQ_MODEL` is set to a model absent from
  the table. Silent wrong-cost accounting is worse than a startup error.
- Add a source comment with the date the pricing was verified, so staleness is
  visible next time.
- Add tests: known model resolves correct metadata; unknown model raises.

**Verify:** show the cost calculation for a sample request before and after.

---

# PHASE 5 — Evaluation set rebuild (target: 2-4 days — this is the main work)

**Problem:** Two linked methodological flaws.

- Golden set documents collapse to a single chunk each
  (`evals/golden/v1/README.md:3-7,30-36`, `evals/harness/metrics.py:69-78`), so
  the reported retrieval hit-rate is document-level, not chunk-level. It cannot
  demonstrate ranking quality. A 100% hit-rate here proves nothing.
- Every unanswerable case uses an empty knowledge base
  (`evals/golden/v1/README.md:16-28`), so the refusal threshold has never been
  tested at its real operating point — a populated KB with an off-topic query.

**Do, in this order:**

1. **Build a multi-chunk corpus.** Documents long enough and structured enough
   that chunking produces multiple chunks per document, with only some of them
   relevant to any given query. Use realistic material from the project's actual
   domain, not synthetic filler.
2. **Label at chunk level.** For each query, record which specific chunk ids are
   relevant, and graded relevance where it applies. Target at least 60-80
   query–chunk pairs. Under 30 is not a usable eval set.
3. **Add populated-KB negatives.** Off-topic and near-miss queries against a
   full knowledge base — questions that sound like they should be answerable but
   are not. These are what actually calibrate the refusal threshold.
4. **Implement ranking metrics** in the harness: recall@k, MRR, and NDCG@k, over
   chunk-level relevance. Keep the existing hit-rate for continuity but stop
   presenting it as the headline number.
5. **Add a faithfulness/groundedness check** that verifies generated claims
   against the retrieved chunks actually used.
6. **Single-command reproducibility.** One documented command runs the whole
   suite and writes a report. Tell me the exact command.
7. **Run it and record the real numbers.** They will be worse than 100%. That is
   the point — a realistic number is credible, a perfect number is not. Write
   down what you actually observed.

**Reminder:** rule 1 applies hardest here. Do not write expected numbers.
Run the harness, report what came out.

---

# PHASE 6 — Parameter sweep (target: 1-2 days, only after Phase 5)

**Problem:** RRF weights, MMR settings, candidate limits, prompt `k`, and
chunking parameters at `hybrid_search.py:6-13,25-30` are library defaults, not
evaluated choices. The repo itself admits the golden set did not exist when
these were chosen.

**Do:**
- Sweep each parameter against the Phase 5 eval set. Chunk size and overlap
  (at least 3 configurations), `k`, RRF weighting, MMR lambda.
- Commit the results table into the repo — including the configurations that
  performed worse. Rejected options are evidence of a real process.
- Update the constants to the measured best values where the difference is
  meaningful, and say explicitly where it was not meaningful.
- If a parameter turns out not to matter, that is a finding worth writing down,
  not a failure.

**Also:** if time allows, compare two embedding models on this corpus and record
the comparison. Right now there is no evidence the current one was chosen rather
than defaulted to.

---

# PHASE 7 — Real-provider adversarial and faithfulness run (target: 1 day)

**Problem:** All current adversarial and faithfulness results are mechanical.
No real model has been evaluated.

**Do:**
- Run the Phase 3 adversarial fixtures against at least two real providers from
  different families.
- Run the Phase 5 faithfulness evaluation against a real model.
- Record results, including failures. A documented failure with a root cause is
  stronger evidence than an undocumented pass.
- Keep credentials out of the repo. Document how someone else would run this.

---

# PHASE 8 — Documentation and trade-offs (target: half a day)

This phase converts the work above into things I can point at in an interview.

**Do:**

1. **Architecture decision records.** One short ADR per major decision: vector
   store choice, embedding model, chunking strategy, hybrid vs dense-only,
   reranker present or absent, LLM provider and generation config. Each ADR:
   what was decided, what alternatives were considered, why this one, what was
   knowingly given up. Where a decision is now backed by Phase 5/6 data, cite it.

2. **Deliberate trade-offs written down as trade-offs.** In particular:
   - **No authenticated provider smoke test in CI.** This is a deliberate
     choice, not an oversight. Document the reasoning — secret management,
     flakiness, cost, and what would change the decision. Do not add the test;
     document why it is absent.
   - Any other place where something is missing on purpose.

3. **README updates.**
   - Real, measured numbers replacing any placeholders — measured only.
   - Latency: p50/p95 with the per-stage breakdown (embed → retrieve → rerank →
     generate) if the data exists; `TBD` if not.
   - Cost per 1000 queries using the corrected Phase 4 metadata.
   - Index lifecycle: how documents are updated and deleted, and whether
     re-indexing is incremental or a full rebuild.
   - **Keep and update the limitations section.** Move fixed items out, leave the
     honest remainder in, and add anything Phase 5-7 newly revealed.
   - Add a stated scale ceiling: at what document count or QPS this design
     stops working, and the specific reason.

4. **Failure log.** A short document with 2-3 real failures observed during this
   work: what happened, how it was diagnosed, what the root cause was, what
   changed. Real ones only — if none occurred, say so rather than inventing
   any.

---

# FINAL DELIVERABLE

After all phases, produce `docs/REMEDIATION_SUMMARY.md` containing:

- Each audit finding, and whether it is **closed**, **partially closed**, or
  **deliberately not addressed** with the reason.
- Every number that changed, old value → new value, and how it was measured.
- Anything you found while working that the original audit missed.
- Anything you were unable to verify, stated plainly.

Then list, in plain language, the three questions an interviewer is now most
likely to ask about this repo, and what the honest answer is.

---

## START

Begin with Phase 1 only. Report back and wait.
