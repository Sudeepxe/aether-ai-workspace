# ADR-4.3: UUIDv7 for all client-visible IDs

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Every client-visible resource ID needs a format decision balancing index-friendliness, non-enumerability, and pagination-key stability.

## Decision

UUIDv7 for all client-visible IDs.

## Alternatives considered

- **Auto-increment integers** — leaks business volume and enables enumeration.

- **UUIDv4** — random ordering fragments B-tree indexes.

## Consequences

Easier: index-friendly inserts, and IDs double as stable cursor material for pagination since UUIDv7 is time-ordered. Harder: none stated beyond the standard adoption cost of a less ubiquitous UUID version.

## Revisit trigger

None stated.
