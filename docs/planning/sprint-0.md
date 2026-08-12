# Aether AI Workspace — Sprint 0 Implementation Plan

**Input:** `AETHER_AI_WORKSPACE_BLUEPRINT.md` (approved, ADR-1.x–11.x binding)
**Sprint goal:** a repository in which *every quality gate exists and passes before any feature code exists* — per sign-off condition (1). Sprint 0 builds the factory, not the product.
**Duration:** 10 working days. **No application code** — scaffolding, configuration, automation, and documentation only.

---

## 1. GitHub Organization Structure

**Decision: single repository under the personal GitHub account — no organization.**

- *Why:* a portfolio flagship's value concentrates in one URL; the contribution graph, stars, and history attribute directly to the personal profile a recruiter actually visits. An org adds a navigation hop and implies a team that doesn't exist.
- *Rejected:* dedicated org (`aether-ai/`) — justified only if the project spawns multiple repos (SDK, docs site) in Phase 3; migration is a rename away, so nothing is lost by deferring. Polyrepo rejected per ADR-9.1.
- **Repo name:** `aether-ai-workspace`. Description = positioning line (§11.5). Topics: `rag`, `llm`, `ai-platform`, `fastapi`, `react`, `pgvector`, `multi-tenant`, `postgres`. Social-preview image: rendered HLA diagram (task, day 9).
- **Profile integration:** pin the repo; personal profile README links to it with the one-paragraph thesis.

## 2. Repository Structure (Sprint 0 subset of §9.1)

Created in Sprint 0 (full tree per blueprint §9.1; items marked ⏳ are placeholder directories with a `README.md` stating what will live there and which sprint delivers it):

```
aether-ai-workspace/
├── README.md · LICENSE · SECURITY.md · CONTRIBUTING.md · CHANGELOG.md (generated, empty)
├── .github/            # workflows/, templates, CODEOWNERS  — fully delivered in S0
├── apps/
│   ├── api/            # package skeleton: empty module tree + tests/ tree — no logic
│   └── web/            # Vite scaffold: builds, renders placeholder, no features
├── packages/contracts/ # ⏳ generated-only (ADR-9.2); README explains the rule
├── evals/              # ⏳ golden/, corpora/, harness/ — S7
├── infra/
│   ├── compose/        # dev profile functional in S0; demo/test ⏳
│   ├── docker/         # Dockerfiles for api/worker/web — build in S0
│   └── secrets/        # SOPS+age bootstrap + encrypted placeholder bundle
├── docs/
│   ├── architecture/   # Blueprint committed, split per chapter, status headers (Ch. 9 F-4)
│   ├── adr/            # ~45 ADRs extracted from blueprint, numbered, + template + index
│   ├── runbooks/       # ⏳ templates only; populated as their systems land
│   ├── api/  · evals/  # ⏳
└── Makefile
```

**Placeholder-directory rule:** every ⏳ README names its delivering sprint — the repo is honest about its own state from day one (mirrors the blueprint's gap-register culture).

## 3. Folder Hierarchy — apps/api Skeleton (empty modules, boundaries enforced)

`src/aether/{domain, app, ports, adapters, modules/{auth, orchestrator, router, retrieval, memory, ingestion, metering}, http, workers}` + `migrations/` + `tests/{unit, integration, contract, security}` — exactly the §3.3/§9.1 shape. Sprint 0 delivers the **import-boundary lint configured and passing against the empty tree**, with one deliberate violation committed on a branch to prove the gate fails (gates are tested by triggering them — D2-3 culture).

## 4. README Outline (S0 skeleton of the §9.2 funnel)

1. Title + one-paragraph thesis (what, and why it is not a demo)
2. Architecture diagram (HLA rendered from Mermaid source)
3. Status block: CI badge (live in S0) · coverage badge (live S1) · eval badge (placeholder "measured from S7" — an honest placeholder, never a fake number)
4. Quickstart: `docker compose --profile dev up` + `make bootstrap` (working in S0 for the infra services)
5. "How it works" → per-chapter links into `docs/architecture/`
6. Roadmap → GitHub milestones (S0–S12 visible)
7. Limitations & gap register (seeded from §10.6)
8. License / Security / Contributing links

## 5. LICENSE

**Apache-2.0** (ADR-9.5: explicit patent grant, fork-friendly for reviewers). NOTICE file included. Eval corpora declared synthetic + project-authored in `evals/corpora/README` (Ch. 9 F-2).

## 6. CONTRIBUTING Guide (outline)

Purpose statement (solo project, team-shaped process) · dev setup (`make bootstrap`, prerequisites, troubleshooting) · branch/commit/PR conventions (§9–11 below) · quality gates and how to run each locally (`make` parity with CI — §10.2 rule) · ADR process: when a change requires one, MADR-lite template, supersede discipline · test standards pointer · "good first issue" note (aspirational, but signals openness).

## 7. CODEOWNERS

`* @<owner-handle>` plus explicit entries for `docs/adr/` and `.github/workflows/` (the two directories where unreviewed change is most dangerous). Solo today; the file is the configuration a team inherits unchanged (ADR-9.4 rationale).

## 8. .gitignore (category plan, per stack)

Python (venvs, caches, coverage artifacts) · Node (node_modules, dist, Vite cache) · env & secrets (`.env*` except `.env.example`; **decrypted SOPS output patterns explicitly listed**) · IDE/OS noise · Docker overrides (`compose.override.yml`) · generated artifacts that CI rebuilds (rendered diagrams, OpenAPI in contracts/ is committed-generated — *exception documented inline*, since ADR-9.2 requires the generated spec to be committed and diffed). Rule: every non-obvious entry carries a comment — a `.gitignore` is documentation.

## 9. Branching Strategy

**Trunk-based:** `main` is the only long-lived branch, always releasable, protected (no direct pushes, required checks, linear history, signed commits enabled).

- Short-lived branches: `feat/<scope>-<slug>`, `fix/…`, `chore/…`, `docs/…`, `adr/…` — target lifetime ≤ 3 days (solo WIP discipline).
- **Rejected:** GitFlow (`develop`/release branches) — ceremony for parallel release trains that don't exist; release = tag on main (§10.3 trunk-based deploys). Rejected: long-lived feature branches — integration debt for one engineer is self-sabotage.
- Releases: milestone completion → annotated tag (`v0.<sprint-group>`, `v1.0.0` at PRR-green) + generated release notes + demo GIF per §9.4.

## 10. Commit Convention

**Conventional Commits**, enforced by commit-lint hook + CI:

- Types: `feat|fix|refactor|perf|test|docs|build|ci|chore|revert`. **Scopes = module/service names from §3.2** (`auth`, `router`, `retrieval`, `ingestion`, `web`, `infra`, `adr`) — the commit log becomes navigable by architecture component.
- Subject: imperative, ≤ 72 chars; body: *why*, not what; footer: `Refs: FR-KB-5` / `ADR-8.6` where applicable — **requirement/ADR traceability in the history itself**.
- Breaking changes: `!` + `BREAKING CHANGE:` footer (relevant from S4 when contracts exist).
- Squash-merge only; PR title must itself be a valid conventional commit (it becomes the main-line message). CHANGELOG generated from history at each tag.

## 11. Pull Request Template (outline)

Sections: **What & why** (1–3 sentences, links issue) · **Blueprint traceability** (FR/NFR/ADR refs; "none" must be argued) · **Type of change** checklist · **Self-review checklist** (the enforced one): gates run locally · new code has tests at the right pyramid layer · no secrets/PII · import boundaries respected · docs/ADR updated or N/A argued · degraded modes considered for any new dependency · **Screenshots/output** for UI or ops changes · **Reviewer notes** (where to start reading; known trade-offs). The template operationalizes the blueprint's self-review culture per-change.

## 12. Issue Templates & Labels

Templates (`.github/ISSUE_TEMPLATE/`): **Task** (sprint work: scope, exit criterion, blueprint refs, DoD checkbox) · **Bug** (repro, expected/actual, severity, logs/correlation-ID) · **ADR proposal** (context, options considered — feeds `docs/adr/`) · **Tech debt** (cost of leaving it, trigger for paying it) · config: blank issues off.

Labels: `sprint:S0…S12` · type (`feat/bug/debt/adr/docs`) · area = module scopes · `severity:{high,med,low}` · `flaky-test` (the §9.5 quarantine class) · `good-first-issue`. **The issue backlog mirrors the S0–S12 roadmap: every sprint's tasks are filed as issues in its milestone during Sprint 0** — visible planning is a deliverable (§9.4).

## 13. GitHub Actions Workflow Plan (no YAML — structural spec)

All workflows: actions **SHA-pinned** (Ch. 9 F-3), concurrency groups cancel superseded PR runs, dependency caching (uv/npm), every job has a `make` twin (§10.2 local-parity rule).

| Workflow | Trigger | Jobs (S0 state) |
|---|---|---|
| `ci.yml` | PR + main | Path-filtered lanes: **lint** (ruff, mypy, eslint, import-boundary, commit-lint) → **unit** (placeholder suites must pass ≥ 1 real test: the boundary-lint test) → **build** (api/worker/web images build; trivy scan) → **security** (gitleaks full-history, pip-audit/npm-audit) → **docs** (link check, Mermaid render, ADR-index consistency). Integration/e2e/eval lanes: defined with `if: false` + comment naming their enabling sprint — the pipeline shape is complete on day one, lanes light up as systems land |
| `deploy.yml` | manual (S0) → main-merge (S11) | Stub: builds, signs (cosign), pushes to GHCR by digest; SSH deploy steps land S11 |
| `eval-nightly.yml` | schedule | Skeleton + budget guard env; enabled S7 |
| `bootstrap-timing.yml` | monthly schedule | **Live in S0** — bare-runner `make bootstrap` timing (Ch. 9 F-1) |
| `restore-drill.yml` | quarterly schedule | Skeleton; enabled S11 |

**S0 acceptance for CI:** a PR that violates each gate class (lint, boundary, secret, unsigned commit) is opened and *demonstrably blocked*, then closed — gate-verification evidence linked from the sprint summary issue.

## 14. Docker Project Structure

- `infra/docker/`: `api.Dockerfile` (multi-stage: uv-builder → slim non-root runtime; worker = same image, different entrypoint per D3-1), `web.Dockerfile` (build → static output; served by Caddy in demo). Images build and pass trivy in S0 even though entrypoints serve only `/healthz` placeholders.
- `infra/compose/`: `compose.yml` + profiles — **`dev` fully functional in S0:** PG (+pgvector image), Redis, MinIO, mailpit, optional Ollama, LGTM stack *stubbed off* until S9; healthchecks on every service; named volumes; isolated network; resource limits stated. `demo`/`test` profiles declared with ⏳ services.
- Standards: pinned image digests, no `latest` (Ch. 10 F-1 discipline applies to *inputs* too), `.dockerignore` per app, build-cache mounts, image-size budgets recorded (api ≤ 350 MB, web ≤ 50 MB) and checked in CI as warnings until S4, gates after.

## 15. Development Environment Setup

- **Prerequisites (documented, version-pinned):** Docker + compose v2, `uv`, Node LTS + npm, `make`, `age`+`sops`, `git` ≥ 2.40 (signing).
- **Make targets (the whole interface, per §9.1):** `bootstrap` (toolchain check → deps → hooks → compose pull → seed env → smoke) · `dev` (compose dev + watchers) · `lint` / `typecheck` / `test` / `test-integration` (⏳ S1) · `build` · `secrets-edit` (SOPS wrapper) · `clean`. Target: `bootstrap` ≤ 15 min bare (NFR-M-1), verified by the monthly workflow from day one.
- **Env contract:** `.env.example` with every variable named + commented + safe default; real secrets only via SOPS bundles (ADR-7.5); a `make` guard fails loudly if `.env` contains entries absent from `.env.example` (drift tripwire).
- Devcontainer: optional secondary path, best-effort (§9.3).

## 16. Coding Standards (binding from first line of code)

- **Python:** ruff (lint+format, strict select set incl. bugbear, no bare `except`, no mutable defaults); **mypy `--strict`** everywhere, no untyped `def`s; import-boundary lint = §3.3 rules (domain imports nothing; adapters only via ports; `modules/*` public-interface rule); async discipline: no blocking I/O on the loop (§4.0 policy — blocking-call detector active in dev/test); docstrings required on public interfaces of `domain/`, `app/`, `ports/` (the parts a reviewer reads), not enforced on adapters' internals; exceptions: domain errors are typed, adapter errors are wrapped at the port boundary (§3.6.1 taxonomy).
- **TypeScript:** `strict: true`, eslint (typescript + react-hooks + jsx-a11y) + prettier; no `any` without an inline justification comment; server state only via TanStack Query (ADR-5.2 as a lint rule where expressible).
- **Naming:** per API conventions §4.2 (snake_case JSON, `*_at`); module vocabulary = blueprint vocabulary (a `Chunk` is called a chunk everywhere).
- **Dependency policy:** every new runtime dependency needs one line of justification in the PR body; Renovate weekly batched, patch-level auto-merge on green (§9.5).

## 17. Linting & Formatting Standards (tool matrix + hooks)

Pre-commit hooks (ordered): ruff-format → ruff → mypy (changed files) → eslint/prettier → import-boundary → gitleaks → commit-lint → `.env.example` drift guard. CI re-runs everything repo-wide (hooks are convenience; **CI is the authority**). Formatting is never debated in review — the formatter is the style guide.

## 18. Testing Standards (binding rules; suites grow from S1)

- Pyramid + gates per §10.5. **Naming:** requirement-traceable where applicable — `test_FR_KB_5_deletion_cascades` pattern (§2.9); given/when/then docstrings on integration+.
- Determinism rules: no sleeps (poll with deadline), no real network in unit/integration (fake ports / testcontainers), frozen clocks via `ClockPort`, seeded randomness. Flaky test ⇒ quarantine label + issue within 24 h (§9.5).
- Coverage: ≥ 80% on `domain/` + `app/` (NFR-M-1); coverage config excludes adapters' vendor-glue with an inline justification list; **coverage may never be raised by excluding files silently** (exclusion changes are PR-flagged).
- Fixtures: factory-based synthetic data only (ADR-9.5); one canonical seed dataset shared by dev profile and e2e.
- S0 deliverable: test harnesses configured for all layers, markers registered, one real test per harness proving the wiring (the boundary-lint test, a schemathesis smoke against the placeholder healthz spec, a Playwright hit of the placeholder page).

## 19. Documentation Standards

- **ADRs:** MADR-lite (Context / Decision / Alternatives considered / Consequences / Revisit trigger), immutable once accepted, supersede-by-link (§9.2). S0 extracts all ~45 blueprint ADRs into `docs/adr/` with a generated index; CI checks numbering + index consistency.
- **Architecture docs:** blueprint split per chapter into `docs/architecture/`, each with a status header (`design | partially implemented (S<n>) | implemented`) updated per sprint (Ch. 9 F-4).
- **Diagrams:** Mermaid source is truth, committed beside its rendered artifact; CI renders and fails on syntax errors.
- **Runbook template:** Symptom → Impact → Diagnosis steps → Remediation → Verification → "Verifying automation" link (the §9.2 testable-docs rule — a runbook without an automation link fails the docs check once its system exists).
- **Prose style:** every claim that can carry a number carries one; every "we will" names a sprint.

## 20. Milestones (GitHub milestones, created in S0)

`S0 Factory` → `S1 Identity & RLS` → `S2 Tenancy + Email` → `S3 Streaming spine` → `S4 Router & budgets` → `S5 Ingestion` → `S6 Grounded chat` → `S7 Evals (North Star baseline)` → `S8 Memory & deletion` → `S9 Observability & chaos` → `S10 Security hardening & API polish` → `S11 Prod & DR` → `S12 v1.0 (PRR)` — each carrying its blueprint §11.6 exit criterion in the milestone description, populated with task issues during S0 (per §12).

## 21. Sprint 0 Deliverables (the checklist)

1. Repo created, protected, licensed, topics + description set
2. Full §2 tree with honest ⏳ placeholders
3. Blueprint committed + split; ~45 ADRs extracted + indexed
4. CI live: lint/build/security/docs lanes green; disabled lanes shaped with enabling-sprint comments
5. **Gate-verification evidence:** one deliberately-failing PR per gate class, blocked, linked from the sprint summary
6. `dev` compose profile up with healthy infra services; images build + scan clean
7. `make bootstrap` green locally *and* on the bare-runner workflow, < 15 min
8. Templates: PR, issues ×4, CODEOWNERS, SECURITY.md, CONTRIBUTING.md
9. SOPS+age bootstrapped; `.env.example` contract + drift guard
10. Milestones S0–S12 created + populated with issues; project board live
11. README skeleton with live CI badge and honest placeholders
12. Sprint 0 summary issue: what shipped, gate evidence links, deviations (if any) with ADRs

## 22. Definition of Done

**Task-level (every issue, from S0 forward):** acceptance criterion met and demonstrated (link/output in the issue) · merged to main via green PR using the template · no quarantined tests introduced · docs/ADR touched or N/A argued · traceability footer present.

**Sprint-level:** all milestone issues closed or explicitly re-scoped (re-scope = comment + label, never silent slip) · sprint summary issue written · `docs/architecture/` status headers updated · demo artifact captured where the sprint's exit criterion is demoable.

**Sprint 0-specific exit:** deliverables 1–12 above complete **and** the factory has *proven* it rejects bad input (deliverable 5) — a pipeline that has never failed is unverified infrastructure.

---

## Sequencing (10 working days)

| Days | Work |
|---|---|
| 1–2 | Repo, protection, license, tree, blueprint/ADR import, templates |
| 3–4 | Toolchains: uv/npm workspaces, ruff/mypy/eslint configs, pre-commit, import-boundary lint (+ its proving test) |
| 5–6 | Dockerfiles + `dev` compose profile; `make bootstrap`/`dev`; SOPS bootstrap; `.env` contract |
| 7–8 | CI workflows (all five), path filters, caching, SHA pinning; gate-verification PRs |
| 9 | README + diagram render pipeline; milestones + issue population; board |
| 10 | Buffer + bare-runner bootstrap verification + Sprint 0 summary; S1 pre-read (Ch. 7/8 sections listed) |

## Sprint 0 Self-Review (per established culture)

| Finding | Resolution |
|---|---|
| F-1: Draft had CI lanes appearing sprint-by-sprint — reviewers would see a *different pipeline shape* each sprint, and "we'll add the gate later" is how gates get forgotten | Full pipeline shape ships in S0 with disabled lanes named-and-dated (`if: false` + enabling sprint) — the factory's final shape is visible and diffable from day one |
| F-2: Gate-verification (deliberately-failing PRs) was missing from the first deliverables draft — gates would exist but be unproven, contradicting D2-3's falsifier principle | Deliverable 5 + DoD clause added; evidence linked from the sprint summary |
| F-3: `.gitignore` risked conflicting with ADR-9.2 (contracts are committed *generated* files) | Exception documented inline in the file itself — the ignore file explains its own contradiction |
| F-4: Issue population for S1–S12 during S0 risks over-planning distant sprints in stale detail | Rule: S1–S3 issues at task grain; S4+ as single milestone-scoped placeholder issues refined two sprints ahead (rolling-wave planning) |

**Verdict:** Sprint 0 plan approved. Exit condition is unambiguous: *the factory works and has been proven to reject defects, before any product code exists.*
