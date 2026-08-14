# ADR-10.5: The v1.0 release gate is a Production Readiness Review checklist, including the eval North Star and a rehearsed rollback

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A v1.0 release needs an unambiguous, falsifiable exit gate rather than a calendar date or a subjective completeness judgment, consistent with the project's philosophy that requirements need falsifiers.

## Decision

Release to v1.0 is gated on a Production Readiness Review checklist: SLOs defined, dashboarded, and alerted; runbooks that have each been executed for real at least once; a restore drill passed within the recovery point and recovery time objectives; security gates green including the cross-tenant red-team suite; the North Star eval scoring at least 90 percent on both faithfulness and correct refusal on the release candidate; a rehearsed rollback exercised via a deliberate bad deploy in staging; enforced cost caps verified via a synthetic burn test; the 15-minute-setup documentation path verified; and a published known-gaps register.

## Alternatives considered

- **Gating release on a calendar date or a subjective completeness judgment** — implicitly rejected; the chapter's framing treats the gap register itself as a Production Readiness Review output, not a confession — the gate is designed to make shipping with known limitations an explicit, audited decision rather than either hiding gaps or blocking release indefinitely.

## Consequences

Easier: v1.0 has an objective, falsifiable exit condition instead of a subjective readiness feeling; the gap register turns known limitations into demonstrated self-awareness rather than a liability. Harder: every checklist item must actually be exercised for real, such as runbooks executed at least once and rollback actually rehearsed — assertions without demonstration don't satisfy the gate.

## Revisit trigger

None stated.
