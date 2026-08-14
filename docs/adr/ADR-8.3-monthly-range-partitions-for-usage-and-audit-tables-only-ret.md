# ADR-8.3: Monthly range partitions for usage and audit tables only; retention enforced by partition drop (D8-3)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

usage_events and audit_events are append-only, time-queried, and subject to retention windows — a partitioning strategy was needed to keep retention enforcement cheap and bound table growth, while messages and chunks don't yet justify the same treatment at MVP scale.

## Decision

Monthly range partitions from day one for usage_events and audit_events only, with retention enforced by dropping whole partitions rather than row-by-row deletion. messages and chunks remain unpartitioned at v1, though partition keys and a migration path are documented for the 100x story.

## Alternatives considered

- **Partitioning all high-growth tables uniformly from day one** — implicitly rejected as premature for messages and chunks at MVP scale, consistent with the project's anti-over-engineering stance; partitioning is applied specifically where its benefit is needed now.

## Consequences

Easier: deleting a month of usage or audit data is a single partition-drop operation, not a tombstone storm across potentially billions of rows. Harder: the partitioning migration path for messages and chunks remains a documented-but-undone future task, to be executed only when the 100x trigger is reached.

## Revisit trigger

messages grows beyond roughly 100 million rows.
