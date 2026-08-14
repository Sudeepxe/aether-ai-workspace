# ADR-11.1: Add an email subsystem and a fully specified password-reset flow (gap remediation)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The Chapter 11 whole-document missing-component sweep found that transactional email was load-bearing, needed for invitations and budget notifications, but had never been designed anywhere in Chapters 1 through 10. Most seriously, password reset, which FR-ID-1 implies but no chapter specified, is a critical auth-adjacent flow that was simply missing.

## Decision

Add an EmailPort in the hexagonal architecture with SMTP and Resend adapters, using mailpit for the local dev profile, already present in the dev compose stack; all sends go via the worker, queue-backed and retried per the standard worker retry policy. The password-reset flow is fully specified: a single-use 128-bit token, hashed at rest, with a 30-minute time-to-live; an enumeration-safe request path; revocation of all active sessions and refresh-token families on a successful reset; auth-class rate limiting; and audit logging. Slotted into Sprint 2, alongside the invitation flow it shares machinery with.

## Alternatives considered

- **Leaving email as an unspecified, ad-hoc integration to be figured out during implementation** — rejected; the whole-document review process specifically exists to catch exactly this kind of load-bearing missing component before implementation begins, not after.

## Consequences

Easier: password reset, an auth-critical flow, now has a fully specified, secure design — enumeration-safe, session-revoking, rate-limited — before any code is written, rather than being improvised under implementation pressure. Harder: adds a new port and adapter and a new Sprint 2 scope item that wasn't accounted for in the original chapter-by-chapter design pass — an accepted cost of the review catching a real gap.

## Revisit trigger

None stated.
