# ADR-5.3: fetch plus ReadableStream SSE client, since native EventSource is unusable (GET-only, no auth header)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The chat streaming contract is POST-initiated, to carry a request body and an Authorization header, but the browser's native EventSource API only supports GET requests and cannot set custom headers.

## Decision

The client implements SSE consumption using fetch() plus ReadableStream with a hand-rolled SSE parser (per the WHATWG streams specification), which supports POST, custom headers, and AbortController-based cancellation — wired to also call DELETE /generations/{gen} on abort, since closing the client-side stream alone does not free server-side provider capacity.

## Alternatives considered

- **Native EventSource** — rejected outright as technically incompatible with the contract (GET-only, no auth header support), not a design preference.

## Consequences

Easier: full control over headers, request body, and cancellation semantics needed by the authenticated, POST-initiated streaming contract. Harder: the team owns and must exhaustively test a hand-rolled SSE parser and stream-lifecycle state machine — called out as the highest-defect-density code in any streaming UI.

## Revisit trigger

The contract moves off POST-based SSE.
