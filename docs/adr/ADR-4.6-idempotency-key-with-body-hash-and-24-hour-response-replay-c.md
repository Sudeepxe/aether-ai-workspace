# ADR-4.6: Idempotency-Key with body-hash and 24-hour response replay; client message_id for chat

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Clients must be able to safely retry mutating POST requests (network retries, timeouts) without risking duplicate side effects, such as duplicate chat turns or double-charged tokens.

## Decision

Mutating POSTs accept an Idempotency-Key header; the server stores the key, a hash of the request body, and the response for 24 hours; a replay with the same key returns the stored response with an Idempotent-Replay: true header. The same key with a different body returns 409. Chat additionally uses a client-generated message_id as a natural idempotency key.

## Alternatives considered

- **No idempotency mechanism (clients must never retry POSTs)** — implicitly rejected, since the Chapter 3 retry table depends on this mechanism to make POST retries safe at all; the design explicitly follows the Stripe model rather than inventing a novel scheme.

## Consequences

Easier: clients may safely retry mutating POSTs, and duplicate chat turns from network retries are prevented rather than merely detected afterward. Harder: the idempotency-key store needs its own lifecycle management (24-hour TTL, reaped by housekeeping) and growth must be monitored.

## Revisit trigger

None stated.
