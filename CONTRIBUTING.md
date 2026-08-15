# Contributing

Solo project, team-shaped process (ADR-9.4): every change goes through a PR
with the template's self-review checklist, required CI, squash-merge with a
Conventional-Commit title.

## Setup

Prereqs: Docker + compose v2, [uv](https://docs.astral.sh/uv/), Node 22 LTS,
make, sops + age, git ≥ 2.40. Then `make bootstrap` (target ≤ 15 min).

## Conventions

- **Branches:** `feat|fix|chore|docs|adr/<scope>-<slug>`, lifetime ≤ 3 days;
  trunk-based, `main` always releasable.
- **Commits:** Conventional Commits; scopes are the Blueprint §3.2 module
  names (`auth`, `router`, `retrieval`, `ingestion`, `web`, `infra`, `adr`);
  footer carries traceability (`Refs: FR-KB-5, ADR-8.6`).
- **Quality gates (all have make twins; CI is authority):**
  `make lint` (ruff, import boundaries, eslint) · `make typecheck`
  (mypy --strict, tsc) · `make test`.
- **Import boundaries** (Blueprint §3.3) are non-negotiable and enforced in
  three places: pre-commit, CI, and the architecture test suite.

## ADRs

An architectural change (new dependency, new boundary, changed contract)
needs an ADR (`docs/adr/0000-adr-template.md`). Accepted ADRs are immutable;
supersede with a new one.

## Testing standards

See `SPRINT_0_PLAN §18` (in `docs/planning/sprint-0.md`): requirement-
traceable names where applicable, no sleeps, no real network in unit tests,
frozen clocks via ports, synthetic fixtures only.
