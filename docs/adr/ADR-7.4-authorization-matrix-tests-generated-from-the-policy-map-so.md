# ADR-7.4: Authorization-matrix tests generated from the policy map, so the matrix cannot drift from code

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The RBAC matrix is only trustworthy if it's provably what the code actually enforces — a hand-maintained test suite alongside a hand-maintained matrix is exactly the kind of parallel truth that drifts silently over time.

## Decision

Authorization-matrix tests are generated directly from the same policy map that is the single source of truth for the RBAC matrix — every route-by-role combination is asserted in CI (roughly 40 routes times 4 roles), so the matrix cannot drift from the code by construction.

## Alternatives considered

- **A hand-written authorization test suite maintained alongside the policy documentation** — implicitly rejected as the drift-prone status quo this decision replaces; the design goal is that the matrix documentation and the enforced behavior are generated from one artifact, not two.

## Consequences

Easier: the RBAC matrix is provably accurate at all times, not just accurate as of when someone last remembered to update the tests; the generated suite is cheap and exhaustive. Harder: any policy-map change automatically ripples into every generated test — by design, since a policy change that doesn't move the tests is exactly the drift this decision exists to prevent.

## Revisit trigger

None stated.
