# ADR-8.5: No production down-migrations; expand-contract only, with an N-1 compatibility CI gate

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Zero-downtime deployment requires that old and new application code can both run correctly against the database schema during a rolling deploy, and that rollback doesn't depend on an untested destructive script written under incident pressure.

## Decision

Migrations are expand-contract exclusively in production: an additive change ships first, then dual-write or backfill, then cutover, with any contracting or cleanup change released later as its own migration. N-1 application code must always run correctly against the N schema. Rollback means redeploying the previous code image, never running a down-migration in production. CI gates every migration against a production-shaped synthetic dataset snapshot plus an explicit N-1 compatibility test suite.

## Alternatives considered

- **Allowing down-migrations as a rollback mechanism in production** — rejected — a down-migration executed under incident pressure is effectively an untested destructive script pointed at the only copy of production data; expand-contract instead makes backward compatibility a designed property rather than a hope exercised for the first time during an incident.

## Consequences

Easier: rollback is pure code rollback, decoupled from schema state, and can happen instantly without any database operation. Harder: every schema change requires the discipline of splitting into an additive step and a later contracting step; the N-1 compatibility gate was itself upgraded from an assertion to a required per-release proof against the actual release-candidate code pair.

## Revisit trigger

None stated.
