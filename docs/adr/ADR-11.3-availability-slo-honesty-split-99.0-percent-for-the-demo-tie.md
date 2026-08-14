# ADR-11.3: Availability SLO honesty split: 99.0 percent for the demo tier, 99.5 percent as the design SLO on the HA profile

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The whole-document contradiction hunt found that NFR-A-1, promising 99.5 percent monthly availability with roughly 3.6 hours of monthly error budget, was incompatible with the single-node demo topology (D10-1), whose rehearsed disaster-recovery recovery time objective is 4 hours — meaning a single node failure alone could exceed the entire month's error budget. Neither the requirements chapter nor the deployment chapter was individually wrong; together they overclaimed.

## Decision

The demo-tier availability SLO is restated to 99.0 percent, with the single-node caveat published explicitly. 99.5 percent is retained as the design SLO, achievable without any architectural change on the documented high-availability upgrade profile, namely managed Postgres plus a second application node.

## Alternatives considered

- **Leaving NFR-A-1 at 99.5 percent unchanged for the demo deployment** — rejected — this would be a dishonest claim given the single-node recovery-time math; the review explicitly treats this as a contradiction requiring resolution, not a rounding error to ignore.

- **Redesigning the demo topology to hit 99.5 percent directly, for example by mandating a second node at demo tier** — implicitly rejected, since the project's cost and operations posture for the demo tier deliberately accepts the single-node trade for the reasons stated in ADR-10.1.

## Consequences

Easier: the published SLO claim is now actually true of the deployed system, closing a gap a careful external reviewer, or an incident, would otherwise expose. Harder: the demo's honest availability ceiling is lower than the architecture is capable of — an explicit, priced, triggered gap, namely the HA upgrade path, rather than a hidden one.

## Revisit trigger

Adoption of the high-availability profile per the priced upgrade path in the Chapter 10 roadmap.
