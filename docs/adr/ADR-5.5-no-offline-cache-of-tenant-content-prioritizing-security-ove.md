# ADR-5.5: No offline cache of tenant content, prioritizing security over convenience

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Offline and flaky-connectivity UX could be improved by caching tenant knowledge-base content client-side (for example, via IndexedDB), but this creates a persistent, unmanaged copy of potentially sensitive tenant data outside the system's deletion and audit controls.

## Decision

No offline-first cache of tenant content — a deliberate security posture that prioritizes avoiding uncontrolled client-side copies of knowledge-base content over offline convenience.

## Alternatives considered

- **Offline-first caching of tenant content (e.g., via IndexedDB or a service worker)** — rejected on security-posture grounds; such a cache would be a copy of tenant data outside the system's deletion and audit controls, in tension with the data-lifecycle principle.

## Consequences

Easier: no additional client-side data-lifecycle surface to reason about for deletion/export compliance. Harder: no offline reading of previously loaded tenant content — users need connectivity to view knowledge-base-grounded history.

## Revisit trigger

An enterprise offline requirement emerges.
