# ADR-9.1: Single monorepo; no monorepo build-graph tooling layer (D9-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

With one engineer, the repository needed a topology decision balancing atomic cross-cutting changes against multi-team-scale tooling needs.

## Decision

One repository containing the API and worker, the web app, shared contracts, infra, docs, and evals; plain workspace tooling (uv workspaces, npm workspaces) plus path-filtered CI for selective execution, with no monorepo build-graph tool such as Nx, Bazel, or Turborepo layered on top.

## Alternatives considered

- **Polyrepo (separate api/web/infra repositories)** — for one engineer this means contract drift between repos, triplicated CI configuration, and cross-repo pull-request dances.

- **Monorepo build-graph tooling (Nx, Bazel, Turborepo)** — build-graph machinery for hundreds of packages, pure ceremony at two apps plus one shared package; rejected per the project's explicit no-over-engineering mandate.

- **A separate public showcase repo plus a private dev repo** — splits commit history, and the history itself — clean commits, PRs, review discipline — is treated as evidence a reviewer should see intact.

## Consequences

Easier: atomic cross-cutting changes, such as an API contract change plus client plus test in one reviewable pull request; one CI entry point; one clone-to-running path. Harder: none of the monorepo-tooling conveniences, such as selective incremental builds via a build graph, are available — mitigated by plain workspace tooling plus path-filtered CI achieving the same selective-execution benefit without the ceremony.

## Revisit trigger

A second team forms, or the repository grows beyond roughly 10 packages.
