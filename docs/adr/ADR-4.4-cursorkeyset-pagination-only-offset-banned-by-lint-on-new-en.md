# ADR-4.4: Cursor/keyset pagination only; offset banned by lint on new endpoints

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

List endpoints need a pagination strategy that stays correct and performant as tables grow and under concurrent writes.

## Decision

Cursor (keyset) pagination on (created_at, id), with an opaque base64 cursor, a limit of at most 100, and next_cursor: null terminating the list — enforced as the only allowed pattern, with offset pagination banned by lint on new endpoints.

## Alternatives considered

- **Offset pagination** — rejected on two independent grounds: performance (O(n) skip cost on hot tables) and correctness (unstable under concurrent inserts/deletes — rows are skipped or duplicated, a consistency bug users can see, not just a performance issue).

## Consequences

Easier: pagination stays performant and correct as tables grow arbitrarily large; UUIDv7's time-ordering (ADR-4.3) makes the cursor key natural. Harder: no "jump to page N" UX is possible with pure cursor pagination — an accepted trade for a chat/document product with no such requirement.

## Revisit trigger

None stated.
