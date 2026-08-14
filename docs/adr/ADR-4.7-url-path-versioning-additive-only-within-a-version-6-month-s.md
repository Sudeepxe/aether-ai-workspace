# ADR-4.7: URL-path versioning, additive-only within a version, 6-month Sunset on breaking changes

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The API needs a versioning strategy that keeps external integrators (the Devon persona) stable while allowing the platform to evolve.

## Decision

URL-path versioning (/v1); additive-only changes within a version (new fields or endpoints never break existing clients); breaking changes require a new version (/v2) plus Deprecation and Sunset headers and a 6-month transition window.

## Alternatives considered

- **Header-based versioning** — hostile to curl-ability, HTTP caching, and support debugging compared to a visible URL path.

- **Stripe-style dated versions with per-account version pinning** — the overhead is justified only at Stripe's scale of client diversity, not for this project.

## Consequences

Easier: clients get a stable, curl-friendly, cacheable versioning scheme with clear deprecation signaling. Harder: /v2 is deliberately a last resort — the additive-only discipline within a version raises the bar for what counts as an acceptable in-place change.

## Revisit trigger

None stated (bounded to one major version migration per two years, maximum).
