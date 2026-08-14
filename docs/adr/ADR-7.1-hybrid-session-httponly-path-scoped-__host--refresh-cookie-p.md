# ADR-7.1: Hybrid session: httpOnly path-scoped __Host- refresh cookie plus in-memory bearer access token (D7-1); BFF as the Phase 3 enterprise path

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Resolves open questions OQ-3.3 and OQ-4.3 — the SPA needs a session and token storage model that defends against both XSS token theft and CSRF, given the product lives entirely behind authentication.

## Decision

A hybrid model: the refresh token lives in an httpOnly, Secure, SameSite=Lax cookie, __Host--prefixed and path-scoped to /v1/auth/refresh; the short-lived access JWT is held in memory only, never localStorage, and sent as a Bearer header. A full backend-for-frontend pattern, where tokens never reach the browser at all, is recorded as the Phase 3 enterprise evolution path.

## Alternatives considered

- **localStorage bearer tokens** — rejected flatly — any XSS exfiltrates long-lived credentials, indefensible in review.

- **Pure server-side sessions (opaque cookie)** — every API request would pay a session-store lookup, CSRF defense would extend to every mutating route, and the external API persona still needs bearer-style credentials anyway, creating two parallel auth paths.

- **Backend-for-frontend (BFF)** — the most secure pattern overall, since tokens never reach the browser, but rejected at v1 as an additional server hop and deployment unit for a solo project.

## Consequences

Easier: splits the two theft vectors — XSS cannot read the httpOnly refresh cookie, and CSRF cannot exploit the bearer header since cross-origin requests can't set custom headers. Harder: the in-memory access token dies on tab refresh or close, requiring a silent re-auth round-trip on boot, coordinated across multiple open tabs.

## Revisit trigger

Enterprise deployment demands a BFF.
