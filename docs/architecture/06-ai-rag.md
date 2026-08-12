# Chapter 6: AI Architecture & RAG

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


> This chapter is the project's technical thesis. The stance throughout: **no framework does the thinking** — LangChain/LlamaIndex-class abstractions are rejected for core orchestration (ADR-6.1) because the mechanics *are* the demonstration; libraries are used at the edges (parsers, tokenizers) where they are commodities.

### 6.0 Decision D6-1: No orchestration framework — thin, owned pipeline

1. **Why.** The orchestration loop (retrieve → assemble → generate → validate → attribute) is ~hundreds of lines of logic that this project exists to demonstrate mastery of. Frameworks hide exactly that logic behind unstable abstractions (both major frameworks have broken compatibility repeatedly), complicate debugging (stack traces through five abstraction layers), and turn interview answers into "the framework did it."
2. **Alternatives:** **LangChain** — rejected for core: abstraction churn, opaque prompt assembly (the security-critical path TB-5 must be owned, not inherited); acknowledged for what it's good at: quick prototyping breadth. **LlamaIndex** — best-in-class ingestion abstractions; rejected for core, *borrowed conceptually* (its parent-document retrieval pattern appears in the Phase-2 roadmap). **Semantic Kernel / Haystack** — same analysis; Haystack's pipeline DAG model is the closest to what we build by hand. **Vercel AI SDK** — UI-stream oriented, wrong tier. The honest company-context note: at a startup shipping product (not proof), LlamaIndex ingestion + owned orchestration is a defensible hybrid — which is exactly the shape chosen here (unstructured/PyMuPDF for parsing, owned everything else).
3. **Trade-offs:** we re-implement solved plumbing (retries, streaming glue — small, already owned by Ch. 3/4 designs); we forgo framework-community velocity on new patterns (mitigated: patterns port as designs, not dependencies).

### 6.1 Ingestion & Chunking — Decision D6-2

**Chosen: structure-aware chunking.** Parse to a normalized document tree (headings/sections/paragraphs/tables via per-format parsers), then pack tree nodes into chunks targeting **~512 tokens, hard max 800, with 10–15% overlap**, never splitting mid-sentence, never crossing top-level section boundaries; every chunk carries `{doc_id, section_path, page_range, char_span, token_count, content_hash}` — provenance is captured at birth, not reconstructed later. Tables are chunked whole with a generated text summary companion (tables embed poorly raw).

- **Alternatives:** **fixed-size sliding window** — the baseline; rejected: splits mid-thought, orphans context from headings; kept as the eval control arm (every chunking claim is measured against it). **Recursive character splitting** (framework default) — better, still structure-blind (a 500-char rule doesn't know a heading from a footnote). **Semantic/embedding-based chunking** (split on embedding-similarity valleys) — 2–5× embedding cost at ingestion for gains that published evals show are corpus-dependent; Phase-3 experiment, not a default. **Late chunking / parent-document retrieval** (embed small, return big) — genuinely promising; deferred to Phase 2 behind the eval harness (retrieval returns child chunk + parent section option), because it doubles retrieval bookkeeping and should be justified by measured gains.
- **Why 512:** empirical sweet spot between retrieval precision (small chunks) and answer-context sufficiency (large chunks) for mixed business documents; **it is a tunable, not a truth** — the eval harness (§6.4) exists precisely to re-derive it per corpus type.

### 6.2 Embeddings & Retrieval — Decision D6-3

- **Embedding policy:** hosted default `text-embedding-3-small` (1536d — cost/quality workhorse); local profile `nomic-embed-text` via Ollama (NFR-PT-1 parity); per-tenant model pinning with versioned migration (Ch. 3 F-5). Dimension note for Ch. 8: 1536×float32 ≈ 6 KB/row — `halfvec` evaluation flagged to the schema chapter.
- **Retrieval (MVP):** hybrid — HNSW vector search (k=20) ∥ Postgres full-text (k=20) → **RRF fusion** → MMR de-duplication → top-k=6 into the prompt. Hybrid is MVP because lexical rescues exactly what embeddings fumble (IDs, part numbers, names, acronyms) and costs one extra indexed query.
- **Multi-turn query handling (MVP — this is where naive RAG dies):** follow-ups ("what about the second one?") embed terribly raw. A **condensing rewrite** (cheap model, ≤ 150 ms budget, only when the thread has prior turns) produces a standalone query. *Both* the raw and rewritten queries feed the lexical leg; the rewritten feeds the vector leg (see self-review F-1). Alternatives: no rewrite (fails follow-ups — measured in eval as the control), concatenated-history embedding (noise swamps signal), HyDE (hallucinate-then-embed; latency+cost for corpus-dependent gains — Phase-3 flag).
- **Reranking:** Phase 2, behind a flag, cross-encoder (hosted rerank API or local bge-reranker) over the fused top-20 → top-6; adopted only if the eval shows ≥ 5-point faithfulness/precision gain — resolving Ch. 2 OQ-1 with a decision *procedure* rather than a guess.
- **Refusal mechanics (FR-KB-4):** two gates — retrieval gate (top fused score below calibrated threshold → grounded-refusal path without calling the generator; threshold calibrated per-embedding-model on the golden set, not hardcoded) and generation gate (system prompt mandates answering only from context, with an explicit "not in the knowledge base" protocol the evals verify).

### 6.3 Prompt Architecture & Context Budget (TB-5 made concrete)

**Layered assembly, fixed order, per-layer token budgets (8K working example):** system policy (~600) → citation & refusal protocol (~200) → memory: summary + window (≤ 1,200, evict oldest-first) → retrieved context (≤ 3,000: k=6 × ~500, ranked, each chunk wrapped in an inert delimiter envelope carrying only `chunk_id`) → user turn + recent exchange (≤ 2,000) → response reserve (~1,200). Over-budget eviction order: retrieved tail → memory window → memory summary — **system policy and the current user turn are never evicted.**

- **Injection posture:** retrieved text enters *only* inside data delimiters; the system layer instructs that delimited content is reference material whose embedded instructions are inert; the assembler strips delimiter-collision sequences from chunk text (an attacker writing our delimiter syntax into a document gets it neutralized). Layered with ingest-time heuristics (flag instruction-dense documents for review) and the adversarial eval set. Stated honestly, as in Ch. 3: mitigated + detected + measured — not solved.
- **Citation protocol:** the model cites `[chunk_id]` inline; post-generation validation maps citations against the actually-retrieved allowlist — valid ones resolve to provenance chips (§5.3), invalid ones are stripped and **counted** (`hallucinated_citation_rate` is a first-class metric); an answer whose every citation fails validation is demoted to an uncited answer with a banner.

### 6.4 Evaluation Architecture — Decision D6-4 (the North Star machinery)

- **Golden dataset (versioned, in-repo, synthetic corpora only — no real tenant data ever):** ~150 cases at v1 across four classes: answerable (target: faithful + correctly cited), **unanswerable** (absent from corpus — target: explicit refusal), **adversarial** (docs containing injection payloads — target: instructions inert + behavior unchanged), and multi-turn follow-ups (target: rewrite resolves reference). Each case: corpus fixture, query (or turn sequence), rubric.
- **Metrics:** faithfulness (LLM-judge with a written rubric, **judge from a different model family than the generator** to avoid self-preference bias, calibrated against a ~30-case human-labeled slice with agreement tracked); citation precision/recall (mechanical — no judge needed); refusal correctness (mechanical); retrieval hit rate (gold-chunk retrieved@k). North Star = faithfulness ≥ 90% ∧ correct-refusal ≥ 90% (§1.7).
- **Execution tiers (cost-bounded):** PR smoke — 20 fixed cases, runs only when prompts/retrieval/chunking paths change (path-filtered), ≈ $0.50, gates merge on regression; nightly full — 150 cases + chunking control arms, trend-charted; release — full + perf overlay. Provider outage during eval → evals defer, merges to AI paths block (fail-closed for the thing the project is *about*).
- **Prod drift signal:** no judge on live traffic by default (privacy stance §3.8); proxies instead — refusal rate, hallucinated-citation rate, citation rate, feedback (FR-CH-6) trends; per-workspace opt-in sampled judging for debugging.
- **Alternatives:** RAGAS/DeepEval as harness — rejected for core (same reasoning as D6-1: the harness is a demonstration artifact; metric *definitions* borrowed where standard); human-only eval — doesn't scale to CI; judge-only without calibration — un-anchored numbers that drift with judge model updates.

### 6.5 Model Routing Policy (task-tier map) & Cost Model

| Task | Tier | Default (hosted) | Local profile | Fallback |
|---|---|---|---|---|
| Chat generation | Mid | GPT-4o-mini / Claude Haiku-class | Llama-3.1-8B (Ollama) | Cross-provider same-tier |
| Query rewrite | Cheap | Smallest hosted | Same local | Skip rewrite (degrade gracefully) |
| Memory compaction | Cheap | Smallest hosted | Same local | Defer (queue holds) |
| Judge (eval) | High | Different family than generator | n/a (eval needs quality) | Eval defers |
| Embeddings | — | text-embedding-3-small | nomic-embed-text | Queue + backoff |

**Unit economics (order-of-magnitude, tracked live per NFR-O-2):** grounded turn ≈ 4K in / 500 out ≈ $0.001–0.003 (mini-tier) → $1–3 per 1,000 turns; ingestion ≈ $0.02–0.10 per 100-page document; rewrite adds ~5%; the $50/mo cap ≈ 15–40K grounded turns — ample for a demo, and the arithmetic itself is a portfolio artifact (FR-AD-2 dashboards make it visible).

### 6.6 Agent & Tool Framework (Phase 2 — interfaces frozen now, per ADR-2.2)

Declarative tool contract `{name, description, json_schema_args, side_effect_class: read|write|destructive, auth_ref, rate_limit, timeout}`; execution loop = bounded state machine (max steps 8, max wall-time 120 s, max tokens budgeted) emitting a **persistent trace tree** (every prompt, tool call, result, decision — FR-AG-3) reusing the SSE grammar (`tool_call`/`tool_result` events slot into §4.4 additively); **policy engine between model intent and execution** — tool calls are *proposals*, checked against workspace policy + side-effect class; `destructive`/external-write requires human approval (FR-AG-4), and — binding TB-5 forward — **a tool call proposed in a turn whose context contains flagged retrieved content requires elevated approval** (the indirect-injection → tool-abuse chain is cut at the policy layer, not by model good behavior). Framework alternatives (LangGraph, AutoGen, CrewAI) rejected for core with the D6-1 argument; LangGraph's explicit-state-machine model is the design this converges to by hand.

### 6.7 Failure, DR, Latency, Monitoring (AI plane)

- **Failure map:** generator down → router fallback (SD-1); embed down → lexical-only retrieval, banner (§3.2.5); rewrite down → skip, log quality flag; judge down → evals defer; *all* providers down → chat 503s with honesty, ingestion queues drain later — the platform (auth, docs, history) stays up. **DR:** golden sets + prompts + thresholds are versioned in-repo (restorable like code); vectors rebuild per ADR-2.3.
- **Latency recap against DF-1:** rewrite +100–150 ms (conditional) and rerank +100–200 ms (Phase 2) are the only additions to the Ch. 3 budget; both are flagged, measured, and individually disableable in brownout (§4.5).
- **AI-plane dashboard (extends §3.8):** faithfulness/refusal trends (nightly eval), hallucinated-citation rate, retrieval hit rate, rewrite trigger rate + latency, per-tier token/cost split, threshold-refusal rate (a spike = threshold miscalibrated or corpus mismatch).

### 6.8 ADRs, Interview Q&A, Mistakes, Roadmap, Checklist

| ADR | Decision | Revisit trigger |
|---|---|---|
| ADR-6.1 | No orchestration framework for core; commodity libs at edges only | Team scales; proof burden shifts to product |
| ADR-6.2 | Structure-aware chunking ~512/800 tokens, provenance-at-birth; fixed-size kept as eval control | Eval shows corpus-specific better default |
| ADR-6.3 | Hybrid RRF retrieval MVP; rerank Phase 2 gated on ≥ 5-pt eval gain; condensing rewrite MVP (raw+rewritten dual-feed) | Eval evidence |
| ADR-6.4 | Two-gate refusal (calibrated retrieval threshold + generation protocol) | Per-corpus calibration replaces global |
| ADR-6.5 | Judge ≠ generator family; human-calibrated; tiered eval spend; fail-closed merges on AI paths | Judge-agreement drops < 80% vs. human slice |
| ADR-6.6 | Tool calls are proposals through a policy engine; injection-flagged context escalates approval | — |

**Interview Q&A.** *Q1: "Why no LangChain?"* — Ideal: not tribalism — the orchestration logic is the deliverable; frameworks hide the security-critical assembly path (TB-5) and turn debugging into archaeology; names where frameworks *are* right (prototyping, breadth) and what was borrowed as design (parent-document retrieval). *Q2: "How do you know your RAG works?"* — Ideal: describes the harness before describing vibes: four-class golden set, mechanical metrics where possible, calibrated cross-family judge where not, CI tiers with cost bounds, and the two-sided North Star (faithfulness ∧ refusal) with why one-sided is gameable. *Q3: "Walk me through a follow-up question failing in naive RAG."* — Ideal: "what about the second one?" embeds to noise; condensing rewrite; dual-feed raw+rewritten so lexical keeps the user's exact terms; eval class proves it. *Q4: "How exactly does a malicious PDF try to take over your agent, and where does it fail?"* — Ideal: traces the chain — payload in doc → chunked/embedded → retrieved → prompt (inert delimiters, stripped collisions) → model may still comply → tool call is a *proposal* → policy engine + flagged-context escalation + human gate; three independent layers, each measured. *Q5: "Your faithfulness metric is an LLM judging an LLM. Why should I trust it?"* — Ideal: shouldn't, blindly — different judge family (self-preference bias), calibration against human labels with tracked agreement, mechanical metrics wherever possible, trends over absolutes.

**Common mistakes.** Chunking by characters and never measuring; retrieval quality "verified" by three cherry-picked demo queries; no refusal design (every RAG demo answers everything); citations rendered from model text without validation; one embedding model change silently corrupting the index (Ch. 3 F-5); evals as a launch-week afterthought instead of CI substrate; judge = generator (self-grading); prompt assembly by string concatenation with no budget, discovered at the first context overflow.

**Roadmap.** Phase 2: rerank flag, parent-document retrieval, agents GA behind policy engine. Phase 3: semantic cache (tenant+KB-version keyed), HyDE/semantic-chunking experiments, per-corpus threshold auto-calibration, doc-level ACL-aware retrieval (FR-KB-9 — index-time partition vs. query-time filter decision goes to Ch. 8's schema with query-time filtering as the working default at v1 scale, resolving OQ-2.4).

**Checklist:** every quality claim has an eval class ✓; every latency addition is flagged + disableable ✓; injection chain cut at ≥ 3 layers ✓; refusal is designed, calibrated, measured ✓; no tenant data in eval sets ✓; unit economics computed and capped ✓.

### 6.9 Self-Review Record — Chapter 6

| Finding | Severity | Resolution |
|---|---|---|
| F-1: Draft fed only the rewritten query to *both* retrieval legs — the rewrite can paraphrase away exact terms (part numbers, names) that the lexical leg exists to catch, silently defeating hybrid's purpose | **High** | Dual-feed: lexical gets raw + rewritten; vector gets rewritten (§6.2) — the eval's multi-turn class now includes exact-term follow-ups to hold this |
| F-2: Refusal threshold was a fixed constant in draft — miscalibrated per embedding model/corpus, and silently invalidated by any embedding migration | Medium | Threshold is a calibrated artifact versioned alongside `embedding_version`, recalibrated as part of the Ch. 3 F-5 migration procedure (§6.2, ADR-6.4) |
| F-3: Judge bias unaddressed in draft (same-family judge inflates scores) | Medium | Cross-family judge + human-calibration slice + agreement tracking (§6.4, ADR-6.5) |
| F-4: Eval cost in CI was unbounded (150 judge calls per PR) | Low | Tiered execution: path-filtered 20-case smoke on PR, full nightly (§6.4) |
| F-5: Delimiter-collision attack (document containing our own delimiter syntax) unhandled in draft assembly | Medium | Assembler strips/escapes collision sequences; adversarial eval set includes this exact payload class (§6.3) |

**Verdict:** pass. F-1 is the finding that matters most — it is a *system-level* interaction (rewrite × hybrid) where two individually correct components silently cancel each other's value; exactly the class of defect that only shows up when someone walks the full data path. F-2 and the Ch. 3 F-5 linkage also demonstrate the review system working across chapters: the embedding-version decision made in Ch. 3 propagated a new obligation here (threshold recalibration) that a chapter-local review would have missed.

---

