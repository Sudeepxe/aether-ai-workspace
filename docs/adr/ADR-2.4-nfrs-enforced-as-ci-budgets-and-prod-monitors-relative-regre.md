# ADR-2.4: NFRs enforced as CI budgets and prod monitors, relative-regression thresholds in CI (D2-3)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

An NFR with no way to fail a build or page an operator is a wish, not a requirement. Every non-functional requirement in Chapter 2 needed a real enforcement mechanism, not just a documented target.

## Decision

Every NFR carries a "verified by" mapping to an automated CI gate (a perf budget in k6, a coverage gate, gitleaks, cross-tenant red-team tests, a deletion verification job) or a production monitor. CI performance budgets use relative-regression thresholds (fail on >20% regression vs. a rolling baseline) rather than brittle absolute wall-clock asserts on noisy shared runners; absolute targets are separately verified nightly on a pinned reference machine.

## Alternatives considered

- **Aspirational NFRs in documentation only** — unverifiable claims rot within weeks, and reviewers know it.

- **Manual pre-release load tests only** — catches regressions only after they've compounded; fine as a supplement, not as the primary mechanism.

- **Prod-only SLO monitoring without CI gates** — a portfolio project has near-zero production traffic, so prod SLOs alone would never exercise the system's limits; the chosen design uses both CI synthetic load as the primary signal and prod monitoring as the secondary one.

## Consequences

Easier: the NFR table becomes an executable contract instead of prose — "my p95 target fails the build if regressed" is a categorically stronger claim than "we aimed for 1.5s." Harder: CI perf tests on shared runners are noisy — mitigated by relative-regression thresholds plus nightly pinned-hardware absolute runs.

## Revisit trigger

At real scale this evolves into SLO error-budget policy (the SRE model).
