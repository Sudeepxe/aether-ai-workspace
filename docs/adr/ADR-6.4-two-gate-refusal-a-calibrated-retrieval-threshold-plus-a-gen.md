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

## Phase 6/7 update (real data, docs/REMEDIATION_PLAN.md)

This ADR's own "Harder" consequence — a single global threshold is a
calibrated artifact that needs recalibrating — turned out to understate
the real problem. A real precision/recall sweep across every distinct
observed fused score, 53 queries (43 positive, 10 negative), found **no
single threshold that meaningfully separates the two classes**
(`evals/golden/v2/PHASE6_RESULTS.md`, Priority 1): the current default
(`0.0082`) sits below every real observed score, so Gate 1 never
refuses (0/10 correctness, matching Phase 5's own measurement). The
best available alternative threshold (≈0.0292) would fix that to 10/10
— at the cost of wrongly refusing 27 of 43 (63%) real answerable
queries, because the two classes' score distributions overlap almost
completely at this corpus scale with this (non-semantic) embedder. The
production constant was deliberately left unchanged rather than trade
one measured failure mode for a larger one.

**Gate 2 is doing real, confirmed backstop work, not just existing on
paper.** Phase 7's real-provider run
(`evals/golden/v2/PHASE7_RESULTS.md`, RUN 2) cross-checked every one of
the 9 "incorrectly refused" answerable queries against Phase 5's own
retrieval data: all 9 are the exact same 9 queries where the correct
chunk never appeared in the top-10 retrieved results at all. Gate 2
never once refused a query whose retrieved context genuinely contained
the answer — for every real, checkable case, it followed its
instruction faithfully given what it was actually shown. Gate 1's
threshold cannot compensate for a genuine retrieval miss; Gate 2's
generation-time instruction is the layer actually catching it in this
environment, not a redundant second check.

**Revisit trigger, made concrete:** the fix this ADR anticipated
("per-corpus calibration") is necessary but, per this real data, not
sufficient on its own — a single global score threshold is the wrong
shape of signal to separate these two classes at this corpus scale and
embedder. A real semantic embedder is the first prerequisite to
re-testing whether calibration alone would work; a different signal
(e.g., a distribution-relative or corpus-relative threshold rather than
one fixed global cutoff) is the likely second one.
