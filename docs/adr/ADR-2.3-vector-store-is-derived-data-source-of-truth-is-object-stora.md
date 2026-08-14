# ADR-2.3: Vector store is derived data; source of truth is object storage plus Postgres

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The MVP scope decision (D2-2) needed a disaster-recovery posture baked in from day one. Whether vector embeddings are treated as a primary store or as a rebuildable derived cache shapes deletion, DR, and migration across the entire system.

## Decision

Vectors are derived data. The source of truth is object storage (original files) plus PostgreSQL (chunks and metadata); vector embeddings are always rebuildable by re-embedding from source.

## Alternatives considered

- **Treating vectors as a primary, independently-backed store** — makes deletion a distributed-consistency problem (dual-write, sagas, reconciliation jobs) and makes DR and embedding-model migration far harder — a heavy price for the project's provable-deletion requirement (FR-KB-5).

## Consequences

Easier: disaster recovery is simplified enormously (no vector backups needed — rebuild from source); deletion becomes provable via cascade; provider/embedding-model swaps become re-embed operations, not migrations. Harder: rebuilding after major data loss costs real re-embedding time and spend (budgeted in Ch. 3/10: ~1M chunks is roughly 2–4 hours and ~$100 of embedding spend).

## Revisit trigger

Revisit only if index rebuild time exceeds the recovery time objective (RTO).
