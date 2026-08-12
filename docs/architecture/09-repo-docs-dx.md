# Chapter 9: Repository Structure, Documentation & Developer Experience

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 9.0 Decision D9-1: Single Monorepo

**Chosen: one repository** — API+worker, web app, shared contracts, infra, docs, evals.

- **Why:** atomic cross-cutting changes (an API contract change + client + test in one reviewable PR); one CI entry point; one clone-to-running path (the 15-minute reviewer, §1.7); a portfolio is *judged as one artifact* — splitting it halves the impression and doubles the maintenance.
- **Alternatives:** **polyrepo (api/web/infra)** — the multi-team default; for one engineer it means contract drift between repos, triple CI config, and cross-repo PR dances; rejected. **Monorepo tooling (Nx/Bazel/Turborepo)** — build-graph machinery for hundreds of packages; at two apps + one package it is pure ceremony; plain workspace tooling (uv workspaces / npm workspaces) + path-filtered CI achieves the same selective execution; rejected per the no-over-engineering mandate. **Separate public "showcase" repo + private dev repo** — splits history, and history *is* evidence (clean commits, PRs, review discipline); rejected.

### 9.1 Repository Layout (normative)

```
aether/
├── README.md                  # The 15-minute path (§9.2) — the most-designed file in the repo
├── LICENSE  · SECURITY.md  · CONTRIBUTING.md  · CHANGELOG.md (generated)
├── .github/
│   ├── workflows/             # ci.yml, eval-nightly.yml, deploy.yml, restore-drill.yml
│   ├── ISSUE_TEMPLATE/  · PULL_REQUEST_TEMPLATE.md   # PR template embeds the review checklist
│   └── CODEOWNERS             # Solo, but the practice is the signal
├── apps/
│   ├── api/                   # Python: FastAPI app + worker (one codebase, two entrypoints — D3-1)
│   │   ├── src/aether/
│   │   │   ├── domain/        # Pure. Imports nothing below this line (lint-enforced, §3.3)
│   │   │   ├── app/           # Use cases; imports domain + ports
│   │   │   ├── ports/         # Interfaces: LLMPort, VectorSearchPort, QueuePort, …
│   │   │   ├── adapters/      # openai/, anthropic/, ollama/, pgvector/, redis/, s3/
│   │   │   ├── modules/       # auth/ orchestrator/ router/ retrieval/ memory/ ingestion/ metering/  (§3.2 catalog, 1:1)
│   │   │   ├── http/          # Routes, middleware, SSE emitters, Problem+JSON mapping
│   │   │   └── workers/       # Consumer entrypoints, per-stream handlers
│   │   ├── migrations/        # Expand-contract only (ADR-8.5)
│   │   └── tests/             # unit/ integration/ contract/ security/  (mirrors Ch. 10 pyramid)
│   └── web/                   # React SPA (Ch. 5): src/{routes,components,stores,api-client(generated),streaming/}
├── packages/
│   └── contracts/             # OpenAPI spec (generated artifact), event schemas, generated TS types
├── evals/
│   ├── golden/                # Versioned datasets: answerable/ unanswerable/ adversarial/ multiturn/  (synthetic only)
│   ├── corpora/               # Fixture documents (synthetic)
│   └── harness/               # Runner config, judge rubrics, calibration labels
├── infra/
│   ├── compose/               # docker-compose.yml + profiles: dev/demo/test (§3.9.1)
│   ├── docker/                # Dockerfiles (api, worker share base; web)
│   └── secrets/               # SOPS+age encrypted bundles (ADR-7.5) — never plaintext
├── docs/
│   ├── architecture/          # This blueprint, split per chapter + rendered diagrams (Mermaid source of truth)
│   ├── adr/                   # ADR-NNNN-*.md — one file per record, MADR-lite template, index
│   ├── runbooks/              # incident-response, restore-drill, key-rotation, dlq-replay, provider-outage
│   ├── api/                   # Published OpenAPI + usage guide (Devon's entry point)
│   └── evals/                 # Latest eval report (the North Star evidence, linked from README)
└── Makefile                   # make bootstrap · dev · test · eval-smoke · demo  — the whole interface
```

Two structural rules carry the weight: **the module tree is the §3.2 service catalog** (a reviewer maps diagram→directory 1:1 — architecture that's visible in `ls`), and **`packages/contracts` holds only generated artifacts** (OpenAPI from FastAPI, TS types from OpenAPI) — contracts are outputs of code, never hand-maintained parallel truths (the Ch. 4 anti-drift stance, made physical).

### 9.2 Documentation Architecture — the 15-Minute Path

**README is engineered as a funnel, in order:** one-paragraph thesis (what + why it's not a demo) → architecture diagram (the §3.1 HLA, rendered) → **live proof block:** CI badge, coverage badge, **eval-score badge (faithfulness/refusal — the badge nobody else has)** → `docker compose --profile demo up` + 90-second demo GIF (upload → grounded answer with citations → refusal case — the refusal is the money shot) → "How it works" links into `docs/architecture/` per chapter → honest limitations section (the §1.8 non-goals, public). The four §1.7 artifacts (diagram, ADRs, one-command demo, eval report) are all ≤ 1 click from the README.

- **ADR process:** MADR-lite (Context / Decision / Alternatives / Consequences / Revisit-trigger), numbered, immutable once accepted — superseding means a *new* ADR linking back (the discipline this blueprint has practiced since ADR-2.1). The blueprint's ~40 ADRs seed `docs/adr/` on day one; implementation adds its own.
- **Runbooks are testable docs:** each names its verifying automation (restore-drill runbook ↔ the scheduled drill workflow; DLQ runbook ↔ the replay script) — a runbook with no executable counterpart is a wish (D2-3's philosophy applied to prose).
- **Docs in CI:** link checker; OpenAPI regenerated and diffed (uncommitted contract drift fails); Mermaid sources rendered (broken diagrams fail); eval report freshness asserted against the nightly run.

### 9.3 Developer Experience & Quality Gates

- **`make bootstrap` ≤ 15 min on a clean machine (NFR-M-1, CI-verified monthly** on a bare runner — the only honest way to keep a setup claim true). Devcontainer as an alternative path (bounded: config maintained best-effort; compose is primary).
- **Pre-commit:** ruff (lint+format), mypy strict, eslint/prettier, gitleaks, import-boundary lint (the §3.3 rules — a domain→adapter import fails *before* CI), conventional-commit message lint.
- **PR discipline (solo, deliberately):** every change via PR with the template's review checklist (self-review is still review — Chs. 3–8 proved the practice); CI required green; squash-merge with conventional titles → generated CHANGELOG + release notes. **The PR history is a portfolio artifact:** a reviewer who reads five PRs sees process, not just product.
- **Branch protection:** no direct pushes to main, required checks, linear history. **CODEOWNERS + branch protection on a solo repo is not theater — it's the configuration a team would inherit unchanged.**

### 9.4 GitHub Presentation Layer

Pinned repo with social-preview image (the HLA diagram); repo topics curated (`rag`, `llm`, `fastapi`, `multi-tenant`, `pgvector`, …); Releases used for phase milestones (v0.x per sprint-group, v1.0 = MVP exit criteria met, each with demo GIF + eval delta); Issues + labels used genuinely (the backlog *is* the Ch. 11 roadmap — visible planning is visible engineering); Discussions off (no community to fragment); `SECURITY.md` with a real contact + 90-day disclosure stance; license **Apache-2.0** (explicit patent grant; MIT acceptable, GPL rejected as friction for the résumé-reader who wants to skim-fork).

### 9.5 Cross-Cutting (chapter rubric applied to the repo itself)

- **Failure scenarios:** CI flakiness is treated as an incident class (quarantine label + tracking issue — flaky tests erode the only reviewer this repo has); GitHub outage → local `make` targets replicate every CI check (CI orchestrates, never *owns*, verification); secrets leak → gitleaks pre-commit + CI + history scrub runbook + rotation drill.
- **Security:** no plaintext secrets ever (SOPS bundles + CI assertion); Dependabot/Renovate weekly batched with auto-merge on patch-level green builds (solo-sustainable dependency hygiene); actions pinned by SHA (supply-chain — third-party actions are unaudited code with your secrets).
- **Scalability (of the repo):** path-filtered CI keeps PR feedback < 10 min as the suite grows; the monorepo's future split line, if ever needed, is `apps/web` — the contracts package already isolates the coupling.
- **Cost:** GitHub free tier covers all of it (Actions minutes bounded by path filters + nightly scheduling); $0.
- **Observability:** CI duration/flake-rate trends reviewed monthly; DORA-lite metrics (deploy frequency, lead time) computable from the git/deploy history — and quotable in interviews.

### 9.6 ADRs, Interview Q&A, Mistakes, Roadmap, Checklist

| ADR | Decision | Revisit trigger |
|---|---|---|
| ADR-9.1 | Single monorepo; no monorepo-tooling layer (D9-1) | Second team or > ~10 packages |
| ADR-9.2 | Contracts are generated artifacts only; hand-edited contracts banned | — |
| ADR-9.3 | Module tree mirrors §3.2 catalog 1:1 (architecture visible in `ls`) | — |
| ADR-9.4 | Solo-PR discipline with required checks; PR history as portfolio evidence | — |
| ADR-9.5 | Apache-2.0; synthetic-only eval corpora (licensing + privacy) | — |

**Interview Q&A.** *Q1: "Monorepo or polyrepo, and does it matter at your scale?"* — Ideal: it matters *differently* — at company scale the driver is team autonomy vs. atomic refactors; solo, the driver is contract integrity and single-artifact presentation; names the tooling trap (Nx at two apps) as the over-engineering tell. *Q2: "How do you keep docs from rotting?"* — Ideal: docs that can fail CI don't rot — generated contracts diffed, links checked, diagrams compiled, eval report freshness-asserted, setup time re-verified on a bare runner; prose that nothing verifies is the rot vector. *Q3: "Why PRs and branch protection with no collaborators?"* — Ideal: process is the demonstrable skill — the checklist catches real defects (cites the Chs. 3–8 self-review records as evidence the practice works), and the config is team-ready as-is. *Q4: "What does your repo tell me in the first 15 minutes?"* — Ideal: walks the README funnel and lands on the eval badge + refusal GIF as the differentiators — every repo has a CI badge; almost none can show a measured hallucination-refusal rate.

**Common mistakes.** README as feature list instead of proof funnel; architecture docs describing a system that drifted three refactors ago (nothing verified them); ADRs written retroactively in one weekend (timestamps tell on you); eval data with scraped/copyrighted or personal content; secrets in git history ("deleted" ≠ gone); unpinned third-party actions; devcontainer as the only path (excludes non-VS-Code reviewers); commit history of "wip", "fix", "final-final" — the portfolio *is* the history.

**Roadmap.** Phase 2: `docs/api` gains SDK examples per endpoint; agent-trace documentation. Phase 3: versioned docs site (only if real external users appear — a docs site for zero users is theater); public roadmap board.

**Checklist:** 15-min path CI-verified ✓; contracts generated-only ✓; module tree ≡ architecture ✓; every runbook has a verifying automation ✓; no plaintext secrets, actions SHA-pinned ✓; license + synthetic-data posture explicit ✓.

### 9.7 Self-Review Record — Chapter 9

| Finding | Severity | Resolution |
|---|---|---|
| F-1: Draft measured "15-minute setup" once, manually — a claim that silently rots as dependencies grow | Medium | Monthly scheduled CI job on a bare runner times `make bootstrap` + smoke test; regression fails the job (§9.3) — the claim now has a falsifier, consistent with D2-3 |
| F-2: Eval corpora provenance unstated — scraped or real-world documents would create licensing/PII exposure *inside the repo* | **High** | Synthetic-only mandate (ADR-9.5): all fixture documents authored for the project; provenance note in `evals/corpora/`; PII scan in CI over the eval tree as a tripwire |
| F-3: Draft omitted supply-chain posture for GitHub Actions (third-party actions run with repo secrets) | Medium | All actions pinned by commit SHA; Renovate updates pins; deploy workflow uses OIDC-scoped, environment-gated secrets only (§9.5) |
| F-4: Blueprint-vs-implementation drift risk — this document will diverge from reality unless the repo owns it | Low | The blueprint is committed as `docs/architecture/` *and split per chapter*, each chapter carrying a "status vs. implementation" header updated per sprint; drift becomes visible, dated, and reviewable |

**Verdict:** pass. F-2 is the catch that protects the project legally, not just technically — eval datasets are the most commonly-overlooked IP/PII surface in AI portfolios. F-4 makes the blueprint itself subject to the same anti-rot machinery it prescribes.

---

