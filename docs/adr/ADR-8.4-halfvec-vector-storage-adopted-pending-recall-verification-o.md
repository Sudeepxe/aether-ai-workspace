# ADR-8.4: halfvec vector storage adopted pending recall verification on the golden set

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A float32 vector row at 1536 dimensions costs about 6KB, and at 1M chunks the resulting 8 to 10GB dataset with HNSW index overhead puts the memory-resident working set under real pressure on the target 8GB VPS.

## Decision

Adopt halfvec(1536), 2-byte floats via pgvector 0.7 or later, as the default vector storage format, halving vector storage and memory footprint, gated on the eval harness verifying no meaningful recall loss (recall@k versus float32) on the golden corpus before the switch is committed to.

## Alternatives considered

- **Staying on float32 vectors** — rejected as the default once halfvec's published benchmarks showed negligible measured recall loss for roughly half the memory cost, directly relieving the HNSW-memory pressure identified as the architecture's first real capacity wall.

## Consequences

Easier: roughly doubles the vector-storage capacity margin on the same hardware, directly extending the runway before the pgvector-to-Qdrant extraction trigger is reached. Harder: the decision is explicitly conditional — if measured recall loss exceeds the stated threshold, the default must revert to float32, so this is a gated adoption, not an unconditional one.

## Revisit trigger

Recall delta exceeds 1 point versus float32 on the golden set, triggering a revert to float32.
