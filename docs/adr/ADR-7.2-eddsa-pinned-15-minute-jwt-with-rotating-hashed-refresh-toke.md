# ADR-7.2: EdDSA-pinned 15-minute JWT with rotating hashed refresh tokens, family reuse-detection, and a 30-second same-device grace window (D7-2)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Token design needed to bound the lifetime of theft, avoid classic JWT algorithm-confusion attacks, and tolerate legitimate multi-tab refresh races without weakening theft detection.

## Decision

Access JWTs are 15-minute, EdDSA (Ed25519)-signed only, an allowlist of exactly one algorithm, with kid resolved only against a static in-process key set (no JWKS-URL fetching from token-supplied input). Refresh tokens are 7-day, opaque 256-bit random, stored hashed, and rotate on every use with family tracking: reuse of an already-used refresh token is treated as family compromise, revoking the entire family and forcing re-authentication. A narrow grace window (30 seconds, same device fingerprint, returns the same successor token rather than issuing a new one) absorbs legitimate multi-tab races without weakening the reuse signal for genuine theft.

## Alternatives considered

- **Opaque access tokens plus introspection** — a store lookup on every request re-couples the hot path to Redis/PG availability, exactly what JWTs are chosen to decouple; high-severity revocation cases are instead handled via the 15-minute TTL plus jti denylist.

- **PASETO** — genuinely better defaults than JWT, but rejected due to thin ecosystem and tooling; the same security properties are achieved by pinning EdDSA and banning dynamic algorithm/key headers.

## Consequences

Easier: token theft is bounded in time on every axis — a 15-minute access window, one-use refresh tokens, revocable API keys — and algorithm-confusion or kid-injection attack classes are eliminated by construction. Harder: the grace window, if scoped too loosely, could itself weaken reuse detection — the chapter's own self-review caught an early looser version of this and narrowed it to same-device plus idempotent-successor semantics specifically to avoid that regression.

## Revisit trigger

None stated.
