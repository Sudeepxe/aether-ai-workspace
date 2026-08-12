# Chapter 2: Requirements & Feature Prioritization

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


> **Review posture for this and all subsequent chapters:** each chapter is an engineering design review, not a product document. Major decisions get the full rubric — rationale, ≥3 rejected alternatives, trade-offs, scalability limits, bottlenecks, security risks, observability, failure handling, cost, and 3-year evolution — and every chapter closes with a Design Review Checklist, Risks, Open Questions, Future Improvements, and Decision Records.

### 2.1 Functional Requirements

Requirements are numbered for traceability — later chapters, tests, and eval cases reference these IDs. Priority uses MoSCoW (Must / Should / Could / Won't-for-v1); the rationale for MoSCoW itself is Decision D2-1 below. Persona codes: MC = Maya Chen, RP = Raj Patel, EV = Elena Volkov, DO = Devon Osei.

#### FR-ID — Identity & Tenancy

| ID | Requirement | Priority | Persona | MVP? |
|---|---|---|---|---|
| FR-ID-1 | Users can register/login with email+password and OAuth2 social login (Google, GitHub) | Must | All | ✅ |
| FR-ID-2 | Users can belong to multiple workspaces; each workspace is an isolated tenant | Must | All | ✅ |
| FR-ID-3 | Workspace owners can invite/remove members via email invitation with expiring tokens | Must | MC, EV | ✅ |
| FR-ID-4 | Role-based access control with at minimum: Owner, Admin, Member, Viewer | Must | EV | ✅ |
| FR-ID-5 | OIDC SSO (enterprise IdP: Okta/Entra ID) with just-in-time provisioning | Should | EV | ❌ Phase 3 |
| FR-ID-6 | SCIM user lifecycle sync (auto-deprovisioning) | Could | EV | ❌ Future |
| FR-ID-7 | Session revocation: admin can terminate any user's sessions immediately | Must | EV | ✅ |

#### FR-CH — Conversations (Threads & Chat)

| ID | Requirement | Priority | Persona | MVP? |
|---|---|---|---|---|
| FR-CH-1 | Users create persistent, titled, searchable threads scoped to a workspace | Must | MC | ✅ |
| FR-CH-2 | Chat turns stream token-by-token to the client (SSE), with cancel mid-stream | Must | MC | ✅ |
| FR-CH-3 | Every assistant message stores: model used, token counts, latency, cost, and (if RAG) retrieved chunk IDs | Must | EV, RP | ✅ |
| FR-CH-4 | Users can regenerate a response, optionally with a different model | Should | MC, RP | ✅ |
| FR-CH-5 | Threads support sharing within workspace (read-only link) | Should | MC | ❌ Phase 2 |
| FR-CH-6 | Message-level feedback (👍/👎 + reason) captured for eval curation | Should | All | ✅ |
| FR-CH-7 | Thread branching (fork a conversation from any message) | Could | RP | ❌ Future |

#### FR-KB — Knowledge Base & RAG

| ID | Requirement | Priority | Persona | MVP? |
|---|---|---|---|---|
| FR-KB-1 | Upload PDF, DOCX, MD, TXT, HTML into a workspace knowledge base (≤50 MB/file v1) | Must | MC | ✅ |
| FR-KB-2 | Ingestion is asynchronous with visible per-document status (queued → parsing → chunking → embedding → ready / failed with reason) | Must | MC | ✅ |
| FR-KB-3 | Chat can be grounded on the KB; answers cite source documents with chunk-level provenance (doc name, page/section) | Must | MC | ✅ |
| FR-KB-4 | When the KB cannot answer, the system says so explicitly rather than hallucinating (ties to North Star refusal metric) | Must | MC | ✅ |
| FR-KB-5 | Documents can be deleted with cascading removal of chunks, vectors, and caches (provable deletion, §1.5) | Must | EV | ✅ |
| FR-KB-6 | Re-ingestion on file update with version supersedence (old vectors retired atomically) | Should | MC | ❌ Phase 2 |
| FR-KB-7 | Collections (folders/tags) to scope retrieval to a subset of the KB | Should | MC | ❌ Phase 2 |
| FR-KB-8 | Connector-based sync (Google Drive, Notion, Confluence) | Could | MC | ❌ Future |
| FR-KB-9 | Document-level ACLs (retrieval respects per-user permissions, not just workspace membership) | Should | EV | ❌ Phase 3 |

#### FR-AG — Agents & Tools

| ID | Requirement | Priority | Persona | MVP? |
|---|---|---|---|---|
| FR-AG-1 | A tool registry: declarative tool definitions (name, JSON-schema args, auth, rate limit) | Must (Phase 2) | RP | ❌ Phase 2 |
| FR-AG-2 | Agent execution loop: plan → tool call → observe → iterate, with hard caps (max steps, max tokens, max wall-time) | Must (Phase 2) | RP | ❌ Phase 2 |
| FR-AG-3 | Full execution trace per agent run (every prompt, tool call, result, decision) viewable in UI | Must (Phase 2) | RP | ❌ Phase 2 |
| FR-AG-4 | Human-in-the-loop approval gates for tools marked destructive/external | Must (Phase 2) | EV, RP | ❌ Phase 2 |
| FR-AG-5 | Built-in tools at launch of Phase 2: web search, calculator, KB retrieval-as-tool | Should | RP | ❌ Phase 2 |
| FR-AG-6 | Sandboxed code-execution tool (containerized, no network, resource-capped) | Could | RP | ❌ Future |

#### FR-AD — Administration & Governance

| ID | Requirement | Priority | Persona | MVP? |
|---|---|---|---|---|
| FR-AD-1 | Immutable audit log: auth events, membership changes, document lifecycle, admin actions, agent tool calls | Must | EV | ✅ |
| FR-AD-2 | Usage dashboard: tokens, cost, requests per user/workspace/model over time | Must | EV | ✅ |
| FR-AD-3 | Per-workspace budgets and per-user quotas with hard/soft limits (§1.5 cost enforcement) | Must | EV | ✅ |
| FR-AD-4 | Workspace-level model policy: allowed providers/models, default model | Should | EV | ✅ |
| FR-AD-5 | Full tenant data export (JSON + original files) and workspace hard-delete | Must | EV | ✅ |
| FR-AD-6 | Configurable data retention windows per workspace | Could | EV | ❌ Future |

#### FR-API — Platform API

| ID | Requirement | Priority | Persona | MVP? |
|---|---|---|---|---|
| FR-API-1 | All product functionality available via versioned REST API (API-first, §1.5) | Must | DO | ✅ |
| FR-API-2 | Scoped, expiring, revocable API keys (workspace-scoped; least privilege) | Must | DO | ✅ |
| FR-API-3 | OpenAPI 3.1 spec auto-generated from code, published with the docs | Must | DO | ✅ |
| FR-API-4 | Streaming chat endpoint usable by external clients (SSE) | Must | DO | ✅ |
| FR-API-5 | Webhooks for async events (ingestion complete, agent run finished, budget threshold) | Should | DO | ❌ Phase 2 |
| FR-API-6 | Official typed client SDK (TypeScript first) | Could | DO | ❌ Future |

### 2.2 Non-Functional Requirements

Every NFR is stated as a measurable target with a verification method — an NFR that cannot fail a build or page an operator is a wish, not a requirement. Targets derive from the Ch.1 scale envelope.

| ID | Category | Requirement (measurable) | Verified by |
|---|---|---|---|
| NFR-P-1 | Performance | Grounded chat p95 time-to-first-token < 1.5 s; ungrounded < 800 ms (excluding provider queue anomalies) | Load test in CI (k6) + prod SLO |
| NFR-P-2 | Performance | Retrieval sub-step (embed query + vector search + rerank) p95 < 400 ms at 1M chunks/tenant | Benchmark suite, tracked per commit |
| NFR-P-3 | Performance | Non-AI API endpoints p95 < 200 ms | Load test + APM |
| NFR-S-1 | Scalability | API tier is stateless: any request servable by any replica; horizontal scale to 5,000 concurrent SSE streams | Architecture review + soak test |
| NFR-S-2 | Scalability | Ingestion scales linearly with workers; 60 docs/min at 2 workers, no head-of-line blocking across tenants | Queue fairness test |
| NFR-A-1 | Availability | 99.5% monthly availability API tier; degraded modes per §1.5 (provider fallback, RAG-unavailable banner) | Uptime monitoring + error budget |
| NFR-A-2 | Availability | Zero-downtime deploys (rolling; in-flight SSE streams drain gracefully ≤ 60 s) | Deploy pipeline test |
| NFR-SEC-1 | Security | Tenant isolation enforced at data layer (Postgres RLS + vector namespace), not only app code; cross-tenant access attempts logged and alerting | Automated cross-tenant test suite (red team tests in CI) |
| NFR-SEC-2 | Security | All secrets in a secret manager; no secrets in env-committed files or images; keys hashed at rest | Static scan in CI (gitleaks) + review |
| NFR-SEC-3 | Security | OWASP ASVS L2 alignment for auth/session/input handling; OWASP LLM Top 10 mitigations documented per item | Security checklist + tests |
| NFR-PR-1 | Privacy | Document hard-delete completes across DB, vector store, and caches ≤ 24 h incl. queue drain; deletion evidenced in audit log | Deletion verification job |
| NFR-O-1 | Observability | 100% of requests carry a correlation ID through API → worker → LLM call; logs structured JSON; traces sampled ≥ 10%, 100% on error | Trace assertion tests |
| NFR-O-2 | Observability | Per-request LLM cost computed and attributed to user+workspace within 5 s of completion | Metering pipeline test |
| NFR-C-1 | Cost | Demo deployment infra ≤ $50/mo; LLM spend hard-capped at $50/mo globally, per-tenant budgets under that | Billing alerts + enforcement |
| NFR-M-1 | Maintainability | ≥ 80% line coverage on core domain logic; every architectural decision has an ADR; new-machine dev setup ≤ 15 min (one command) | CI gates + docs test |
| NFR-D-1 | Disaster recovery | RPO ≤ 1 h (WAL archiving), RTO ≤ 4 h for full restore incl. vector rebuild from source docs | Quarterly restore drill (scripted) |
| NFR-PT-1 | Portability | Runs fully locally (compose + Ollama) with zero cloud dependencies; cloud deploy differs only in config | CI job runs e2e suite against local profile |

**Traceability rule:** every NFR above must be claimed by exactly one later chapter (perf → Ch. 3/6, security → Ch. 7, observability → Ch. 10, etc.). Unclaimed NFRs at the end of the blueprint are a review failure.

### 2.3 Feature Prioritization — MVP vs. Future

**MVP definition (one sentence):** a user can sign up, create a workspace, invite a teammate, upload documents, and receive streaming, cited, grounded answers that refuse when the KB lacks the answer — with real auth, tenant isolation, budgets, audit logging, observability, tests, and a one-command deployment.

| Phase | Scope | Exit criterion |
|---|---|---|
| **MVP (Phase 1)** | FR-ID-1..4, ID-7; FR-CH-1..4, CH-6; FR-KB-1..5; FR-AD-1..5; FR-API-1..4 | North Star eval ≥ 90/90; all NFR CI gates green; demo deployed |
| **Phase 2** | Agents & tools (FR-AG-1..5), webhooks, KB re-ingestion & collections, thread sharing | One non-trivial agent (web-search + KB) with full trace UI and approval gates |
| **Phase 3** | Enterprise: OIDC SSO, doc-level ACLs, semantic caching GA, TS SDK | SSO login against a real IdP (Okta dev tenant) in demo |
| **Future / Won't-v1** | SCIM, connectors, sandboxed code exec, retention policies, thread branching, mobile | — |

---

### 2.4 Decision D2-1: MoSCoW as the prioritization framework

1. **Why chosen.** This is a single-engineer product with zero usage data. MoSCoW's output — an unambiguous scope contract ("Won't" is a first-class answer) — is exactly what the project needs, and it demonstrates scoping discipline to a repo reviewer. Its weakness (no intra-bucket ranking) is covered by dependency-ordered phases in Ch. 11.
2. **Alternatives considered.**
   - **RICE (Reach × Impact × Confidence ÷ Effort).** Rejected: Reach and Impact require real user data; with none, the numbers would be invented, and invented precision is worse than honest coarseness. Interviewers spot pseudo-quantification instantly.
   - **Kano model.** Rejected: requires user surveys to classify delighters vs. basics; no users exist. Its core insight (grounded citations are a "basic," agents are a "delighter") is borrowed informally in the phase ordering instead.
   - **Weighted scoring matrix.** Rejected: weights are as subjective as the ranking they produce; adds a spreadsheet without adding information. Defensible at a company where weights are negotiated across stakeholders — there is one stakeholder here.
3. **Trade-offs accepted.** MoSCoW can hide effort differences inside "Must" (a 2-day Must and a 3-week Must look identical); mitigated by the phase table carrying explicit exit criteria, and Ch. 11 carrying effort estimates.
4. **Evolution (3-year).** If the project gained real users, prioritization would migrate to RICE fed by product analytics (FR-CH-6 feedback events are already designed to feed this).

### 2.5 Decision D2-2: RAG-grounded chat before agents (the MVP scope cut)

This is the most consequential decision in the chapter — it defines what the platform *is* for its first phase.

1. **Why chosen.** RAG exercises the entire platform spine end-to-end: async ingestion workers, queueing, vector storage, prompt assembly, streaming, citations, evals, cost metering. Shipping it forces every production system (auth, tenancy, observability, CI) to exist. It also directly produces the North Star metric, which is the repo's headline evidence. Agents, by contrast, are the highest-complexity, highest-security-risk subsystem (arbitrary tool execution) and depend on primitives (tool registry, policy engine, trace store) that don't exist yet.

2. **Alternatives considered (3).**
   - **Alt A — Agents-first MVP.** Maximize wow factor; RAG later as "just another tool."
     *Rejected:* agents without grounding are demos, not products — the anti-persona table (§1.10) shows tool execution is the largest attack surface, and shipping it first means shipping the hardest security problem with the least mature platform underneath it. Also unevaluable: agent quality has no clean metric analogous to faithfulness/refusal, so the repo's headline claim would be anecdotal.
   - **Alt B — Everything in parallel.** Build chat, RAG, and agents simultaneously toward one big launch.
     *Rejected:* for a single engineer this maximizes WIP and integration risk, and eliminates the intermediate proof points (a reviewable, deployed MVP) that a portfolio needs. Big-bang integration is also where solo projects die.
   - **Alt C — Chat-only MVP (no RAG).** Fastest to ship; add RAG in Phase 2.
     *Rejected:* fails the differentiation test (§1.4) — a multi-tenant chat wrapper is precisely the "student project" failure mode this project exists to avoid. It also defers the hardest data problems (ingestion, deletion, isolation of embeddings) which is where the architectural signal lives.
   - **Alt D — RAG-as-a-library** (skip the platform; ship an excellent retrieval library + CLI).
     *Rejected:* demonstrates AI engineering but abandons the backend/system-design portfolio goals (auth, tenancy, deployment). Noted honestly: this would be the right call if the goal were an OSS library rather than a platform.

3. **Trade-offs.** The differentiating agent feature is delayed a full phase; the platform risks looking like "another RAG chat" until Phase 2 lands. Mitigation: the tool-calling interface is *designed* in Ch. 6 now (interfaces frozen early, implementation deferred), so Phase 2 is additive, not disruptive.

4. **Scalability limits of the chosen scope.** The MVP's scaling-critical paths, in order of expected failure: (a) ingestion pipeline — embedding API rate limits bind before CPU; (b) SSE fan-out — connection count binds before request throughput; (c) vector search latency past ~10M chunks/tenant without index sharding. These become the load-test targets in Ch. 10.

5. **Bottlenecks.** Single shared embedding-provider quota is a cross-tenant head-of-line risk → NFR-S-2 mandates per-tenant queue fairness (weighted fair queuing at the worker, designed in Ch. 3).

6. **Security risks introduced by this scope.** Indirect prompt injection arrives in MVP (uploaded docs → prompts), so the §1.5 untrusted-content principle must be implemented in Phase 1, not deferred with agents. Deletion (FR-KB-5) is MVP because retrofitting provable deletion is near-impossible.

7. **Monitoring & observability.** MVP ships with the full observability stack (NFR-O-1/2) because Phase 2 agent debugging is impossible without traces already in place — observability is a Phase-1 dependency of a Phase-2 feature.

8. **Disaster recovery / failure handling.** MVP includes the restore drill (NFR-D-1): vector stores are rebuildable from source documents by design (source-of-truth = object storage + Postgres; vectors are a derived cache). This single decision — *vectors are derived data* — simplifies DR enormously and is recorded as ADR-2.3.

9. **Infrastructure cost.** MVP demo: single VPS (4 vCPU/8 GB, ~$24/mo) running compose, or free-tier split (Fly.io/Render + Neon Postgres + Upstash Redis) at ~$0–15/mo; object storage pennies; LLM spend capped $50/mo. Phase 2 adds ~$10/mo (worker instance). Detailed in Ch. 10.

10. **3-year evolution.** Phase ordering anticipates it: RAG spine (Y1) → agents + connectors (Y1–2) → enterprise (SSO/ACL/SCIM, multi-region read replicas) (Y2–3). The invariant across all three years: tenant isolation and the eval harness never get rebuilt — everything else may.

### 2.6 Decision D2-3: NFRs as CI-enforced budgets, not documentation

1. **Why chosen.** Every NFR in §2.2 carries a "verified by" column that maps to an automated gate (perf budget in k6, coverage gate, gitleaks, cross-tenant red-team tests, deletion verification job) or a prod monitor. This converts the NFR table from prose into an executable contract — and in an interview, "my p95 target fails the build if regressed" is a categorically stronger claim than "we aimed for 1.5 s."
2. **Alternatives.** (a) *Aspirational NFRs in docs only* — rejected: unverifiable claims rot within weeks and reviewers know it. (b) *Manual pre-release load tests* — rejected: catches regressions after they've compounded; fine as a supplement, not the mechanism. (c) *Prod-only SLO monitoring without CI gates* — rejected as sole mechanism: a portfolio project has near-zero prod traffic, so prod SLOs alone would never exercise the limits; CI synthetic load is the primary signal here, prod monitoring the secondary. (Chosen design uses both.)
3. **Trade-offs.** CI perf tests on shared runners are noisy → budgets use relative regression thresholds (fail on >20% regression vs. rolling baseline) rather than absolute wall-clock asserts; absolute targets verified on a pinned reference machine nightly.
4. **Evolution.** At real scale this becomes SLO error-budget policy (SRE model): release velocity throttled by budget burn, not by calendar.

---

### 2.7 Interview Questions & Ideal Answers (Chapter 2 scope)

**Q1. "How did you decide what made the MVP?"**
*Ideal answer:* Names a mechanism, not vibes: MoSCoW for scope contract; the cut line chosen so the MVP exercises the full platform spine (async workers, vector store, streaming, evals) and produces the headline metric; agents deferred because they're the largest attack surface and depend on primitives the MVP builds. Bonus: names what was *rejected* (chat-only MVP) and why.

**Q2. "Your NFR says p95 first-token < 1.5 s. Walk me through the latency budget."**
*Ideal answer:* Decomposes it: auth/routing ~20 ms, query embedding ~50–100 ms, vector search ~50 ms, rerank ~100–200 ms, prompt assembly ~10 ms, provider TTFT ~400–800 ms — and identifies provider TTFT as the dominant, least-controllable term, which is why the target excludes provider queue anomalies and why semantic caching is the main lever.

**Q3. "Why is document deletion a Must for MVP? It's a portfolio project."**
*Ideal answer:* Deletion is architectural, not a feature: it requires vectors/caches to be modeled as derived data with cascade paths from day one. Retrofitting it means auditing every copy of data ever made. Also GDPR-shaped thinking is exactly what separates production engineers from demo builders.

**Q4. "What would you cut first if you had half the time?"**
*Ideal answer:* Shows the priority stack is real: cut FR-CH-4 (regenerate), FR-AD-4 (model policy), social login (keep email+password) — never cut tenancy, deletion, evals, or observability, because those are unretrofittable or are the proof the project exists to produce.

**Q5. "How do you verify a non-functional requirement?"**
*Ideal answer:* Every NFR has a falsifier: a CI gate, a scheduled job, or an alert. Gives the deletion example (verification job queries all stores for tombstoned doc IDs) and the noise problem with CI perf tests (relative-regression thresholds + nightly pinned-hardware runs).

### 2.8 Common Junior-Engineer Mistakes (requirements phase)

1. Writing NFRs without numbers ("the system should be fast and secure") — unverifiable, therefore meaningless.
2. Treating deletion, tenancy, and audit as "later features" — the three least-retrofittable properties in the system.
3. MVP defined by effort ("what I can build in a month") instead of by proof ("what demonstrates the thesis") — yields a smaller product, not a smaller *complete* product.
4. Prioritizing the demo-impressive feature (agents) before the platform that makes it safe and debuggable.
5. No traceability: requirements that no test, chapter, or metric ever references again — requirements theater.
6. Confusing availability of the app with availability of dependencies — no defined degraded modes for provider outage.
7. Pseudo-quantified prioritization (RICE scores invented without data) — false precision that collapses under one interview question.

### 2.9 Production Best Practices Borrowed from Large Tech Companies

- **Amazon — Working Backwards:** the MVP definition is written as a one-sentence user outcome (§2.3) before any technology is named; the press-release test ("would this MVP be announceable?") shaped the cut line.
- **Google — design-doc culture:** explicit Non-Goals (§1.8), named alternatives with rejection rationale (every D2-x), and the rule that unreviewed requirements don't get built.
- **Google SRE — SLOs with error budgets:** availability targets stated with measurement windows and a defined enforcement consequence (D2-3 evolution), not marketing-grade "99.99%".
- **Microsoft SDL — threat modeling at requirements time:** anti-personas (§1.10) exist before architecture, so security requirements (NFR-SEC-*) are inputs to design, not audit findings after it.
- **Meta/Stripe — requirements as tests:** each Must-level FR maps to acceptance tests in Ch. 11; the requirement ID appears in the test name (`test_FR_KB_5_deletion_cascades`).

### 2.10 Design Review Checklist — Chapter 2

- [x] Every FR has an ID, priority, persona traceability, and MVP disposition
- [x] Every NFR is measurable and names its verification mechanism
- [x] MVP is defined by proof-of-thesis, not by effort ceiling
- [x] The scope cut (RAG before agents) has ≥3 rejected alternatives with reasons
- [x] Unretrofittable properties (tenancy, deletion, audit, observability) are all MVP
- [x] Security requirements trace to named threat actors from §1.10
- [x] Cost has both a tracking and an enforcement requirement
- [x] DR has numeric RPO/RTO and a drill requirement
- [ ] NFR ownership map (which chapter claims each NFR) — to be completed as Chs. 3–11 land; open until final review

### 2.11 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| MVP scope still too large for one engineer → stalls mid-phase | Medium | High | Phase exit criteria are demoable increments; §2.7 Q4 cut list pre-agreed |
| Embedding/LLM provider quota changes invalidate perf NFRs | Medium | Medium | Provider-agnostic layer (§1.5); local-model CI profile (NFR-PT-1) as floor |
| CI perf gates too noisy → developers ignore red builds (alarm fatigue) | Medium | High | Relative thresholds + nightly pinned-hardware absolute runs (D2-3) |
| "Won't-v1" list erodes under enthusiasm (scope creep) | High | Medium | Adding any Won't item requires a new ADR superseding D2-2 |

### 2.12 Open Questions (carried into later chapters)

1. Is reranking (NFR-P-2's 100–200 ms) in the MVP retrieval path, or a Phase-2 quality lever? → decide in Ch. 6 with eval data.
2. Postgres+pgvector vs. dedicated vector DB at the 1M-chunks/tenant target → Ch. 3/8 decision with benchmarks.
3. Are per-workspace budgets enforced pre-request (estimate) or post-request (settle + suspend)? → Ch. 4.
4. Does FR-KB-9 (doc-level ACLs) require retrieval-time filtering or index-time partitioning? → Ch. 6/7; affects vector schema, so flagged early.

### 2.13 Future Improvements

Feedback-driven reprioritization once FR-CH-6 data exists; formal capacity model replacing the static scale envelope; requirement-coverage tooling (lint that fails if a Must FR has no linked test).

### 2.14 Decision Records

| ADR | Decision | Status | Superseded-by risk |
|---|---|---|---|
| ADR-2.1 | MoSCoW prioritization for v1 (D2-1) | Accepted | Migrate to RICE if real usage data appears |
| ADR-2.2 | MVP = grounded RAG chat; agents deferred to Phase 2 with interfaces designed in Ch. 6 (D2-2) | Accepted | Low — phase exit criteria guard it |
| ADR-2.3 | Vector store is derived data; source of truth = object storage + Postgres; vectors rebuildable | Accepted | Revisit only if index rebuild time exceeds RTO |
| ADR-2.4 | NFRs enforced as CI budgets + prod monitors, relative-regression thresholds in CI (D2-3) | Accepted | Evolves into SLO error-budget policy at real scale |

---

