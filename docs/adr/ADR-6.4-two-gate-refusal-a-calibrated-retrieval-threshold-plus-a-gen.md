# ADR-6.4: Two-gate refusal: a calibrated retrieval threshold plus a generation protocol

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

FR-KB-4 requires the system to explicitly refuse rather than hallucinate when the knowledge base cannot answer a question — one half of the project's two-sided North Star metric — and needed a concrete mechanism, not just a prompt instruction.

## Decision

Two independent refusal gates: a retrieval gate, where the top fused retrieval score must clear a threshold calibrated per embedding model on the golden set (below threshold triggers a grounded-refusal path without even calling the generator), and a generation gate, where the system prompt mandates answering only from provided context with an explicit "not in the knowledge base" protocol that the eval suite verifies.

## Alternatives considered

- **Prompt-only refusal (relying solely on generation-time instructions)** — implicitly insufficient on its own, which is why a separate, earlier retrieval-score gate exists as a first line of defense before the generator is even invoked.

## Consequences

Easier: an empty or low-confidence retrieval becomes a designed, cheap outcome (no generator call) rather than an expensive hallucination risk. Harder: the threshold is an embedding-model-specific calibrated artifact that must be recalibrated whenever the embedding model or version changes, linked to the Chapter 3 embedding-version migration procedure — flagged by the chapter's self-review as a risk if left as a fixed constant.

## Revisit trigger

Per-corpus calibration replaces the current global threshold.
