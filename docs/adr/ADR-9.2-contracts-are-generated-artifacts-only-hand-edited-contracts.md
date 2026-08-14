# ADR-9.2: Contracts are generated artifacts only; hand-edited contracts are banned

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

API contracts (the OpenAPI spec, generated TypeScript types) can either be hand-maintained as a parallel source of truth alongside the code, or generated from the code itself — hand-maintained contracts are a classic drift vector.

## Decision

packages/contracts holds only generated artifacts: the OpenAPI spec generated from FastAPI and Pydantic models, and TypeScript types generated from that spec. These are committed, so CI can diff for drift, but never hand-edited.

## Alternatives considered

- **Hand-maintained contract files as the source of truth, with code implementing the contract** — rejected as precisely the drift vector this decision exists to eliminate; contracts must be outputs of code, never a hand-maintained parallel truth.

## Consequences

Easier: the published API documentation and the actual behavior structurally cannot drift apart, since one is generated from the other; CI can fail on uncommitted contract drift. Harder: the .gitignore file must carry an explicit, documented exception for this directory, since it's normally instinctive to ignore generated files.

## Revisit trigger

None stated.
