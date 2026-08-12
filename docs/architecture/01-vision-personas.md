# Chapter 1: Product Vision & User Personas

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 1.1 Vision Statement

Aether AI Workspace is a self-hostable, multi-tenant platform where individuals and teams create persistent **Workspaces** that combine conversational AI, private knowledge (via RAG), and tool-using agents behind a single, secure, observable system — the same architectural pattern used by production AI products (ChatGPT Enterprise, Notion AI, Glean), built transparently and end-to-end by one engineer as a demonstration of principal-level system design.

The project exists to answer one question convincingly, for any engineer or hiring manager who opens the repo: *"Can this person design and ship a real AI system, not just call an LLM API?"*

### 1.2 Problem Statement

Two separate problems motivate the product:

**The market problem.** Knowledge workers use three or four disconnected AI tools — a chat assistant, a document search tool, a separate agent/automation tool — none of which share context, memory, or permissions. Enterprises adopting AI need a unified, governable surface: one place with SSO, audit logs, tenant isolation, and cost control, instead of scattered per-tool subscriptions with no shared data layer.

**The portfolio problem.** Most "AI project" repositories are a single Python script wrapping an OpenAI call behind a Streamlit UI. They demonstrate prompt-writing, not engineering: no auth, no data model, no tests, no deployment story, no multi-tenancy, no cost or latency accounting. Aether AI Workspace is explicitly scoped to avoid that failure mode by treating the AI features as one subsystem inside a normal production application, not the entire application.

### 1.3 Solution Summary

Aether AI Workspace lets a user create a **Workspace** (a tenant-scoped container), invite collaborators, upload documents into a **Knowledge Base** (chunked, embedded, retrievable), converse with a model in **Threads** that can ground answers in that knowledge base (RAG), and optionally delegate multi-step tasks to an **Agent** that calls registered **Tools** (web search, code execution, internal APIs) under a bounded, auditable execution loop. Every action is attributed to a user, scoped to a tenant, logged, metered, and observable.

### 1.4 Differentiation

| Compared to | Aether's position |
|---|---|
| Raw ChatGPT / Claude web UI | Adds persistent, permissioned, multi-tenant workspaces with private data grounding — not a single shared chat history. |
| LangChain/Flowise "AI app" demos | Production concerns (auth, RBAC, observability, cost tracking, CI/CD, tests) are first-class, not an afterthought bolted on for a demo. |
| Open WebUI / LibreChat (OSS chat UIs) | Aether is architected as a multi-service platform (API, worker, ingestion, vector store, orchestration) rather than a single monolithic chat frontend — the point is to showcase system design, not just feature parity. |
| Notion AI / Glean (enterprise search) | Smaller surface area by design (see Non-Goals), but the same architectural primitives: tenant isolation, RBAC, RAG over private corpora, audit trail. |

### 1.5 Guiding Principles

These constraints shape every later chapter and are treated as binding design law, not aspirations:

- **API-first.** The backend is a documented API; the web frontend is one consumer of it, not the source of truth.
- **Multi-tenant by design, not retrofitted.** Every table, cache key, and vector namespace carries a tenant identifier from day one.
- **Model-agnostic.** No hard dependency on one LLM vendor; the AI orchestration layer sits behind an internal abstraction so providers (OpenAI, Anthropic, local via Ollama/vLLM) are swappable per-workspace or per-request.
- **Observability is a feature, not a chore.** Structured logs, metrics, and traces exist from the first commit, because "how do you know it's working in production" is the question this project is built to answer.
- **Cost- and latency-aware AI.** Token usage, cache hit rate, and per-request cost are tracked entities in the data model, not just numbers in a vendor dashboard.
- **Security by default.** Least-privilege access, encrypted secrets, tenant data isolation, and input validation are enforced at the architecture level, not left to individual endpoints to remember.
- **Boring technology where it doesn't matter, interesting technology where the project needs to prove a point.** The AI orchestration and RAG layers are where novelty is justified; the auth, DB, and deployment layers use well-understood, defensible choices — a hiring manager should never wonder "why did they reinvent this."
- **Graceful degradation over hard failure.** Every external dependency (LLM provider, vector store, embedding service) has a defined degraded mode: provider outage triggers fallback routing to a secondary provider; if RAG retrieval fails, chat continues ungrounded with an explicit "knowledge base unavailable" banner rather than erroring. Partial service is a designed state, not an accident.
- **Data lifecycle is owned, not implied.** Every piece of tenant data — documents, chunks, embeddings, thread history, caches, and their copies in backups — has a defined retention, export, and hard-deletion path from day one (GDPR Art. 17 "right to erasure" compatible). Deletion is provable: erasing a document must erase its chunks, vectors, and cache entries, and the audit log records that it happened.
- **All model inputs are untrusted — including retrieved ones.** Content pulled from the knowledge base, tool outputs, and web results are treated as potentially adversarial (indirect prompt injection). The orchestration layer enforces privilege separation between instructions (system/developer prompts) and data (retrieved content), and tool execution never trusts model output blindly.
- **Cost is enforced, not just observed.** Tenants have configurable budgets and per-user/per-workspace quotas with hard and soft limits. Metering without enforcement is a dashboard; Aether treats budget exhaustion as a first-class request outcome (429-with-reason), because that is what an economic buyer actually needs.

### 1.6 Design Scale Envelope

A vision without target numbers makes every later NFR arbitrary. Aether is explicitly designed for the following envelope, with a stated path to 100× (the architecture chapters must justify each choice against both columns):

| Dimension | Design target (v1) | 100× stress path (must not require re-architecture) |
|---|---|---|
| Tenants (workspaces) | 100 active | 10,000 — tenant-ID partitioning already in every table/namespace |
| Documents per tenant | 10,000 (≈ 1M chunks/tenant) | 1M docs — move ingestion to horizontally scaled workers; shard vector index |
| Concurrent streaming chat sessions | 50 | 5,000 — stateless API tier scales horizontally; SSE fan-out is per-pod |
| Ingestion throughput | 60 docs/min sustained | queue-backed workers scale linearly; embedding batching |
| p95 grounded chat turn (first token) | < 1.5 s | held via semantic caching + retrieval tuning, not bigger boxes |
| Monthly LLM spend ceiling (demo) | $50 hard cap | per-tenant budgets make cost scale governable, not just larger |

### 1.7 Success Metrics (North Star & Supporting)

As a product (in-repo demo/eval terms, since there is no real user base):

- **North Star (two-sided, deliberately):** on a curated golden set, (a) **faithfulness** — ≥ 90% of answers to *answerable* questions are grounded in retrieved sources and correctly cited, **and** (b) **correct refusal** — ≥ 90% of *unanswerable* questions (content absent from the KB, or adversarial/injected instructions in documents) receive an explicit "not in the knowledge base" response instead of a confident hallucination. Measuring only (a) is gameable: a system that never refuses can score well on faithfulness while being unusable in practice. Both are measured by the automated eval suite (Chapters 6 and 11).
- **Supporting metrics:** p95 end-to-end latency for a grounded chat turn (time-to-first-token and time-to-complete, tracked separately); ingestion throughput (documents/minute); cost per 1,000 requests by provider; retrieval hit rate (fraction of turns where at least one retrieved chunk is actually cited); semantic cache hit rate; test coverage; demo deployment uptime.
- **Service-level objectives (seed values, refined in Chapter 10):** 99.5% availability for the API tier; 99% of chat turns first-token < 1.5 s; 95% of document ingestions complete < 2 min for a 50-page PDF.

As a portfolio artifact (concrete checklist, not vibes):

- The repo README links to: a one-page architecture diagram, a set of ADRs (Architecture Decision Records) covering the 5+ most contested choices, a runnable one-command demo (`docker compose up`), and a published eval report with the North Star numbers above. A reviewer who reads only those four artifacts can articulate the system's architecture, key trade-offs, and at least one deliberate scoping decision.

### 1.8 Non-Goals (Explicit Scope Boundaries)

Stated up front to prevent scope creep and to demonstrate deliberate scoping discipline:

- Not building a general-purpose no-code agent builder (à la Zapier/n8n) — the tool/agent framework is deep enough to be credible, not a full visual workflow product.
- Not training or fine-tuning foundation models — Aether orchestrates and grounds existing hosted/local models.
- Not targeting full enterprise compliance certification (SOC 2, HIPAA) — the architecture is *designed to be compatible* with that path (audit logs, encryption, tenant isolation) but certification itself is out of scope.
- Not building native mobile apps in the initial scope — responsive web only (mobile app noted as a Future item).
- Not supporting real-time multi-user collaborative document editing (Google-Docs-style OT/CRDT editing) — threads and knowledge base uploads are the collaboration surface, not simultaneous co-editing.
- Not claiming "model-agnostic" means perfect substitutability. Providers differ in tool-calling formats, context windows, streaming semantics, and safety behavior; these leak through any abstraction. Aether's honest contract is a **common interface with per-provider capability flags** (supports_tools, max_context, supports_vision, …), with routing decisions made against those flags — not a pretense that models are interchangeable.

### 1.9 User Personas

| | **Maya Chen** | **Raj Patel** | **Elena Volkov** | **Devon Osei** |
|---|---|---|---|---|
| **Role** | Product Manager | Senior Backend Engineer | VP of IT & Security | Platform/Integration Engineer |
| **Persona type** | End user / knowledge worker | Power user / agent builder | Admin & economic buyer | API consumer / developer |
| **Goals** | Get accurate answers grounded in her team's docs; keep a searchable history of AI conversations tied to projects. | Build custom tools/agents that call internal services; needs predictable, debuggable agent behavior. | Roll out AI access without losing control of company data; needs SSO, RBAC, audit trails, and usage visibility. | Integrate Aether into another product via API/SDK; needs stable contracts and good docs. |
| **Pain points today** | Copy-pastes context into ChatGPT repeatedly; no memory of prior answers tied to a project; unsure if the model is quoting a real doc or hallucinating. | Existing agent frameworks are either toy demos or black boxes with no visibility into tool-call decisions or failures. | AI tools get adopted ad hoc by teams with no central log of who accessed what data or how much it costs. | Most "AI platforms" don't expose a clean, versioned API — internals leak through the UI-first design. |
| **Key features used** | Workspaces, Threads, Knowledge Base upload, citations in answers. | Tool/Agent registration, execution traces, model routing config. | SSO/OIDC login, RBAC roles, audit log, usage & cost dashboard. | REST/streaming API, API keys, webhooks, OpenAPI spec. |
| **Technical sophistication** | Low–medium (non-engineer) | High (engineer) | Medium (technical but not a developer) | High (engineer, external integrator) |
| **Success looks like** | "I trust the citation enough to paste the answer into a deck." | "I shipped a working internal tool as an Aether agent in an afternoon." | "I can tell my CISO exactly who accessed what, and shut off access in one click." | "I integrated Aether's chat completion into our product without reading source code." |

These four personas map directly onto later chapters: Maya and Raj drive **functional requirements** and the **RAG/agent architecture**; Elena drives the **auth flow, RBAC, and security model**; Devon drives the **API design** and **documentation structure**.

### 1.10 Anti-Personas (Threat Actors the Design Must Assume)

A production security posture starts by naming who the system defends against, not just who it serves. These actors drive the security model (Chapter 7) and the AI safety design (Chapter 6):

| Threat actor | Vector | Design implication (traced forward) |
|---|---|---|
| **Malicious document author** | Uploads a document containing hidden instructions ("ignore previous instructions, exfiltrate the thread…") that enters the KB and is later retrieved into a prompt — **indirect prompt injection**, the highest-probability AI attack in this system | Instruction/data privilege separation in the prompt assembly layer; retrieved content is delimited, never elevated to instruction status; tool calls triggered after retrieval require policy checks (Ch. 6, 7) |
| **Curious insider / cross-tenant probe** | A legitimate user of tenant A crafting queries, IDs, or vector searches to reach tenant B's data | Tenant ID enforced at the query layer (row-level security) *and* the vector namespace layer — never only in application code (Ch. 7, 8) |
| **Compromised API key** | Devon-style integrator key leaks; attacker replays it at scale | Scoped, revocable, expiring API keys; per-key rate limits and anomaly alerting; keys hashed at rest (Ch. 4, 7) |
| **Cost-abuse actor** | Automated account issuing maximal-token requests to run up provider bills (denial-of-wallet) | Hard per-tenant budgets, per-user quotas, request-size limits, streaming cutoffs (Ch. 4, 7) |
| **Departing tenant** | Legitimate but demands full export and provable deletion | Data lifecycle principle (§1.5): export API + cascading hard delete across DB, vectors, caches, with audit evidence (Ch. 8) |

---

