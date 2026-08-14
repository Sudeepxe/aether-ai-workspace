# ADR-4.5: Deny-by-default route registration: undeclared authorization is a boot failure

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The OWASP API5 "missing function-level authorization" bug class is normally caught by review vigilance, which does not scale reliably even for a careful solo engineer.

## Decision

Deny-by-default route registration — every route must declare its required role or scope at registration time, or the process fails to boot. A missing authorization declaration is a boot error, not a runtime vulnerability waiting to be discovered.

## Alternatives considered

- **Relying on code review or convention ("we always add the decorator")** — implicitly insufficient; the entire point of this decision is to move the guarantee from social process to construction.

## Consequences

Easier: an entire class of authorization bugs is prevented by construction rather than caught by review; CI can additionally assert spec-to-registry parity. Harder: every new route incurs an unavoidable upfront authorization-declaration step — a deliberate friction, not an accident.

## Revisit trigger

None stated.
