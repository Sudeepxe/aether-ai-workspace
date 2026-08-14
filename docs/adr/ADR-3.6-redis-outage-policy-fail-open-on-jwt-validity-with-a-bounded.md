# ADR-3.6: Redis-outage policy: fail open on JWT validity with a bounded 15-minute exposure window

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Session revocation checks (the JWT jti denylist) depend on Redis; a Redis outage forces a choice between failing open (availability, bounded security exposure) and failing closed (total auth outage).

## Decision

On Redis outage, fail open on JWT validity — revocation checks become unavailable but already-issued tokens continue to be honored, bounded by the token's remaining time-to-live (a maximum 15-minute exposure window), with a loud alert fired immediately.

## Alternatives considered

- **Fail closed (reject all requests when revocation checks are unavailable)** — converts a cache-tier outage into a total authentication outage, judged worse than a bounded, alerted security exposure window.

## Consequences

Easier: a Redis outage degrades security posture only, not overall availability — authentication stays up. Harder: an already-revoked token could remain honored for up to 15 minutes during the outage; the trade is deliberate and bounded by the short access-token TTL.

## Revisit trigger

If the threat model hardens for a Phase 3 enterprise deployment, add a JWT introspection fallback path.
