# ADR-9.3: The module tree mirrors the §3.2 service catalog one-to-one, so architecture is visible in ls

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A reviewer evaluating the project's architecture should be able to verify the documented service catalog against the actual code layout without cross-referencing a separate mental map.

## Decision

Each §3.2 logical service catalog entry is a top-level package under apps/api/src/aether/modules/ (auth, orchestrator, router, retrieval, memory, ingestion, metering), each with an explicit public-interface module; cross-package imports of anything non-public fail the import-boundary lint.

## Alternatives considered

- **Organizing code by technical layer only, without a service-catalog-mirroring module structure** — implicitly rejected, since the explicit design goal is that a reviewer maps diagram to directory one-to-one.

## Consequences

Easier: architecture is verifiable by running ls, not just by reading documentation that could have drifted from the code; future service extraction becomes moving a package and swapping its interface implementation for a network client, rather than a redesign. Harder: module boundaries must be actively maintained via lint enforcement, or the one-to-one mapping degrades over time like any other unenforced convention.

## Revisit trigger

None stated.
