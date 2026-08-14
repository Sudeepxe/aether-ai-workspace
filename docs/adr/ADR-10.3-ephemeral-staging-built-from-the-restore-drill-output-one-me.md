# ADR-10.3: Ephemeral staging built from the restore-drill output, one mechanism serving two duties

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A persistent staging environment doubles infrastructure cost to protect what is, at this stage, a demo rather than a revenue-generating product; separately, the disaster-recovery posture requires a regularly rehearsed restore drill anyway.

## Decision

Staging is deliberately ephemeral: the demo stack is redeployed to a parallel compose project on the VPS only for release candidates, built from a restored, synthetic production snapshot. The scheduled restore-drill workflow is the staging provisioner — one mechanism serves both the pre-production-smoke and migration-rehearsal duty and the disaster-recovery-drill duty.

## Alternatives considered

- **A persistent, always-on staging environment** — rejected deliberately, judged to double cost purely to protect a demo with no real users yet.

## Consequences

Easier: the restore drill's regular quarterly execution doubles as continuous validation that staging provisioning actually works, rather than the two being separately maintained, potentially drifting systems. Harder: no long-lived soak environment exists between release candidates — mitigated by a pre-release soak run executed as its own step.

## Revisit trigger

Persistent staging becomes justified when real users exist.
