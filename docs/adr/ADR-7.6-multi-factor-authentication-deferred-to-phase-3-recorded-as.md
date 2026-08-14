# ADR-7.6: Multi-factor authentication deferred to Phase 3, recorded as a known, stated gap

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Multi-factor authentication is a standard enterprise security expectation, but implementing it (TOTP/WebAuthn flows, recovery codes, admin enforcement policy) is nontrivial scope that isn't required to prove the platform's core RAG, tenancy, and security architecture.

## Decision

MFA is explicitly deferred to Phase 3 and recorded as a known, stated gap in the security posture — published in the security checklist and gap register rather than silently omitted.

## Alternatives considered

- **Shipping a minimal MFA implementation in v1 to avoid the gap** — implicitly rejected as scope the MVP's proof-of-thesis doesn't require, per the Chapter 2 scoping discipline: unretrofittable properties like tenancy, deletion, and audit are MVP, but MFA is retrofittable and not core to the RAG platform thesis.

## Consequences

Easier: MVP scope stays focused on the platform's core thesis rather than expanding into every enterprise auth feature. Harder: v1 genuinely lacks MFA — an honest, published limitation rather than a silent one, explicitly not treated as a security incident when reported.

## Revisit trigger

Enterprise tier or real users.
