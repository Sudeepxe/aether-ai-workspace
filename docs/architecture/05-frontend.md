# Chapter 5: Frontend Design

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 5.0 Decision D5-1: React 18 + TypeScript + Vite, pure SPA (no SSR)

1. **Why chosen.** The entire product lives behind authentication — there is no SEO surface, no public content, no first-paint-from-server benefit that SSR monetizes. A Vite SPA is the smallest system that fully serves the product; every part of it (static files) deploys from a CDN/reverse proxy with zero server runtime, which keeps the deliberately API-first architecture honest: the frontend holds no privilege and no logic the public API lacks (Amazon mandate, §4.12). React is chosen for ecosystem depth (streaming-UI patterns, virtualization, headless component libraries) and — candidly — hiring-signal legibility.
2. **Alternatives.**
   - **Next.js (SSR/RSC).** The industry default, and the right answer for anything with a public marketing/SEO surface. *Rejected:* adds a Node server runtime, a second deployment target, and RSC's client/server mental-model complexity — all to render pages that require a JWT to see. Cost without benefit here; noted honestly as the default choice in a company context where the marketing site and app share a codebase.
   - **SvelteKit.** Technically excellent, smaller bundles. *Rejected* on ecosystem thinness for this project's needs (mature virtualization, headless a11y primitives) and signal legibility; a defensible personal-taste choice elsewhere.
   - **HTMX / server-rendered.** Architecturally interesting, and a good fit for CRUD-heavy admin tools. *Rejected:* the core surface is a streaming, optimistic, stateful chat client — exactly the workload that outgrows hypermedia patterns.
   - **Angular.** Full-framework weight and opinionation exceed a single-product SPA's needs; no differentiated benefit for this design.
3. **Trade-offs.** SPA pays first-load cost → bounded by a **performance budget: ≤ 250 KB gzipped initial JS**, route-level code splitting (markdown/highlight/admin bundles lazy), and Vite chunking; no-JS accessibility is sacrificed knowingly (product is inherently interactive).

### 5.1 Decision D5-2: State Architecture — Server State ≠ Client State

**TanStack Query owns all server state** (threads, documents, usage — fetched, cached, invalidated, retried per §4.2 semantics); **Zustand owns the small residue of true client state** (composer draft, active stream buffer, UI preferences); **no global store for server data, ever.**

- **Alternatives:** **Redux (+RTK Query)** — rejected: its value is centralized *complex client* state, which this app doesn't have; RTK Query duplicates TanStack Query with more ceremony. **React Context for everything** — rejected: re-render storms in a token-streaming UI (context invalidates whole subtrees per token — unusable). **MobX** — fine engine, weaker ecosystem pull; no differentiated win.
- **The streaming exception (deliberate):** in-flight token streams bypass the query cache — tokens append to a Zustand buffer driving only the active message component (renders batched via rAF, ~30 fps flush, so a 100-token/s stream doesn't cause 100 re-renders/s); on `done`, the settled message is written into the TanStack cache and the buffer cleared. Server state stays canonical; streaming stays fast.

### 5.2 SSE Consumption — the detail that breaks naive designs

**Native `EventSource` cannot be used:** it is GET-only and cannot carry `Authorization` headers or a POST body — the §4.4 contract is POST-initiated. The client therefore uses **`fetch()` + `ReadableStream` with an SSE parser** (per the WHATWG streams spec), which supports POST, headers, and `AbortController` cancellation (wired to the `DELETE /generations/{gen}` call — abort the read *and* tell the server, since closing a response stream alone doesn't free provider capacity).

**Stream lifecycle state machine (per message):** `composing → submitted (optimistic insert, client message_id) → streaming (tokens, seq-deduped) → settled | partial | cancelled | errored`. Reconnect on heartbeat loss (45 s, §4.4) re-issues with `Last-Event-ID`; duplicate `seq` events discarded (exactly-once rendering). If the resume buffer has expired server-side, the client reconciles from the persisted message (fetch by `message_id`) and offers regenerate — every path lands in a defined state; there is no "spinner forever."

**Optimistic UI + idempotency:** the user's message renders immediately with the client-generated `message_id`; a network-level retry of the POST is harmless (ADR-4.6 replay). Reconciliation rule: server response is always authoritative; optimistic entries carry a `pending` flag until acknowledged.

### 5.3 Component & Route Architecture

- **Routes:** `auth/*`, `w/{ws}/chat/{thread?}`, `w/{ws}/knowledge`, `w/{ws}/knowledge/search` (the Raj debug surface — resolves OQ-4.1: yes, the SPA consumes `POST …/search` directly to show raw retrieval results with scores), `w/{ws}/settings/{members|budget|api-keys|audit}`, `w/{ws}/usage`. Workspace ID in the URL mirrors path-explicit tenancy (F-2, Ch. 4) — deep links are tenant-unambiguous.
- **Component strategy:** headless primitives (**Radix UI**) + **Tailwind** for styling — full a11y behavior (focus, ARIA, keyboard) without owning a design system's maintenance. *Rejected:* MUI/Chakra (theme-fighting, bundle weight), hand-rolled primitives (a11y is where hand-rolling silently fails).
- **Long-thread performance:** message list virtualization (TanStack Virtual) is **required, not optional** — threads grow unboundedly. Streaming + virtualization interact badly (auto-scroll vs. user scroll-back); rule: pin-to-bottom only while the user is at bottom; a "jump to latest" affordance otherwise (see self-review F-2).
- **Markdown rendering is a security surface:** model output renders through a constrained pipeline — markdown parser → sanitizer (allowlist; no raw HTML pass-through) → highlighter (lazy-loaded) — under a strict CSP (no `unsafe-inline` scripts). Citations render as chips resolved from the `citation` events; a citation ID not in the event stream renders as plain text (client-side twin of the Orchestrator's citation validation).

### 5.4 Cross-Cutting: Errors, Offline, A11y, i18n

- **Error boundaries per route** + typed API-error mapping (Problem+JSON `code` → user-appropriate message; correlation ID surfaced in a "report" affordance). Stream errors are *states*, not exceptions (§5.2).
- **Offline/flaky:** TanStack Query retries idempotent reads (jittered); mutations never auto-retry beyond the idempotent design; a connectivity banner appears on repeated failures. No offline-first cache of tenant data (a deliberate security posture — no IndexedDB copies of KB content).
- **A11y:** streaming responses announced via `aria-live="polite"` region (batched announcements, not per-token — screen-reader flooding is a real streaming-UI failure); full keyboard nav via Radix; WCAG 2.1 AA contrast as a lint gate.
- **i18n-readiness:** all strings through a message layer from day one (retrofitting i18n is another unretrofittable); v1 ships English only.

### 5.5 Frontend Observability, Testing, Cost

- **Observability:** Web Vitals + custom marks (time-to-first-token *as experienced by the client* — the metric that closes the loop on NFR-P-1 from the user's side) shipped to a first-party `/telemetry` endpoint (no third-party analytics — consistent with the log-privacy stance §3.8); client errors sampled with correlation IDs joining backend traces.
- **Testing (frontend slice of the Ch. 10 pyramid):** Vitest + Testing Library for components/hooks (the SSE parser and stream state machine get exhaustive unit tests — they are the highest-defect-density code in any streaming UI); Playwright e2e for the golden paths (login → upload → grounded chat with citation → refusal case) with a **mocked SSE server for determinism** plus one live-stack smoke; visual regression on the chat surface (Playwright screenshots); a11y automated pass (axe) in CI.
- **Cost:** static hosting ≈ $0 (served by the existing reverse proxy); no third-party SaaS.

### 5.6 ADRs, Interview Q&A, Mistakes, Roadmap, Checklist

| ADR | Decision | Revisit trigger |
|---|---|---|
| ADR-5.1 | React+TS+Vite SPA, no SSR; 250 KB gz initial budget (D5-1) | Public/SEO surface appears |
| ADR-5.2 | TanStack Query (server state) + Zustand (client state); streaming bypasses cache with rAF-batched buffer (D5-2) | — |
| ADR-5.3 | fetch+ReadableStream SSE client (native EventSource unusable: GET-only, no auth header) | Contract moves off POST-SSE |
| ADR-5.4 | Radix + Tailwind headless approach | Design-system team exists (never, solo) |
| ADR-5.5 | No offline cache of tenant content (security > convenience) | Enterprise offline requirement |

**Interview Q&A.** *Q1: "How do you render a 100-token/s stream without killing React?"* — Ideal: separate the write path (buffer outside React state, rAF-batched flush ~30 fps) from the read path (only the active message subscribes); settled messages enter the normal cache; names re-render blast radius as the core concern. *Q2: "Why can't you just use EventSource?"* — Ideal: GET-only, no headers/body ⇒ incompatible with authenticated POST-initiated streams; fetch-streams + AbortController, and cancellation must also notify the server (client disconnect ≠ freed provider capacity). *Q3: "Where does server state live and why not Redux?"* — Ideal: server state is a *cache*, not app state; TanStack Query's staleness/invalidation model fits; Redux solves complex client state this app doesn't have; the streaming buffer is the one principled exception. *Q4: "What's your XSS posture for model output?"* — Ideal: model output is untrusted input (TB-5 extended to the client); sanitizer allowlist, no raw HTML, strict CSP, citation-ID validation client-side; defense in depth with the server-side sanitization.

**Common mistakes.** Rendering every token through global state (unusable at streaming rates); EventSource discovery at integration time (a whole-sprint surprise); no terminal state for interrupted streams (eternal spinners); localStorage tokens (XSS-stealable — Ch. 7 owns the decision); skipping virtualization until threads are slow (retrofit is painful with streaming autoscroll); per-token `aria-live` announcements (screen-reader flooding); shipping MUI's bundle to render two buttons.

**Roadmap.** Phase 2: agent-run trace viewer (tree UI over FR-AG-3 traces), thread sharing views, webhook config UI. Phase 3: SSO login flows, semantic-cache-aware latency hints, workspace theming. Out: native mobile (§1.8).

**Checklist:** budget enforced in CI (bundle-size gate) ✓ design; every stream path lands in a defined state ✓; a11y automated + manual pass ✓ plan; no tenant data in persistent client storage ✓; client TTFT metric closes NFR-P-1 loop ✓; OQ-4.1 resolved ✓.

### 5.7 Self-Review Record — Chapter 5

| Finding | Severity | Resolution |
|---|---|---|
| F-1: **Multi-tab token refresh race** — N tabs detect expiry simultaneously and race the refresh endpoint; with rotation + reuse detection (Ch. 7), the losers' reuse of the old refresh token can trip family revocation and log every tab out | **High** | Cross-tab coordination: `BroadcastChannel` + Web Locks — one tab refreshes, broadcasts the new access token; others wait. Requirement registered against Ch. 7's rotation design (reuse detection must tolerate a small grace window as backstop) |
| F-2: Virtualized list + streaming autoscroll conflict unstated in draft — auto-follow fights user scroll-back and re-measures during token growth | Medium | Pin-to-bottom only when at bottom; height re-measure batched with the rAF flush; "jump to latest" affordance — now normative in §5.3 |
| F-3: Draft omitted what happens when the server resume buffer has expired mid-reconnect | Medium | Defined: reconcile from persisted message via `message_id` + offer regenerate (§5.2) — no undefined stream states |
| F-4: Optimistic insert had no reconciliation rule on conflict (e.g., replayed POST returns existing message) | Low | Server-authoritative rule + `pending` flag semantics added (§5.2) |

**Verdict:** pass. F-1 is the real catch — invisible until a user opens a second tab, and it manifests as "the app randomly logs me out," one of the hardest bug classes to reproduce. It creates a binding requirement on Chapter 7 (grace window in reuse detection), demonstrating why frontend and auth design cannot be reviewed in isolation.

---

