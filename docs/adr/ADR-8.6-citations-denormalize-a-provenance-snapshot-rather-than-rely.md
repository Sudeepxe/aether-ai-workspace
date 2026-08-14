# ADR-8.6: Citations denormalize a provenance snapshot rather than relying on a live chunk foreign key

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A direct conflict existed between two requirements: FR-KB-5 requires documents and their chunks to be fully, provably deletable, while message_citations rows are foreign-keyed to chunks for provenance — a naive cascade-delete would silently rewrite conversation history by making citations vanish from old answers, while a restrict-delete would block deletion entirely and violate FR-KB-5.

## Decision

message_citations denormalizes a provenance snapshot (document title, section path, page) at write time, rather than relying solely on a live foreign key to chunks; the chunk foreign key becomes nullable-on-delete, and the UI renders a "source removed" state for citations whose underlying chunk has since been tombstoned by deletion.

## Alternatives considered

- **Cascade delete of citations when their chunk is deleted** — would silently rewrite the historical record of what a past answer cited, an integrity violation.

- **Restrict delete (block chunk deletion while citations reference it)** — directly violates the provable-deletion requirement, which the project treats as non-negotiable.

## Consequences

Easier: document deletion completes fully and provably while historical conversation provenance remains intact and honest, rather than silently corrupted. Harder: citation data is now denormalized, a deliberate, minimal duplication, accepted specifically because it's exactly the fields provenance requires and nothing more.

## Revisit trigger

None stated.
