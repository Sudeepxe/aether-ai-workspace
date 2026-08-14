# ADR-6.2: Structure-aware chunking at roughly 512/800 tokens with provenance captured at birth; fixed-size kept as an eval control

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Chunking strategy directly determines retrieval precision and answer-context sufficiency; a decision was needed on how documents are split into retrievable units and how provenance is tracked.

## Decision

Parse documents to a normalized tree (headings, sections, paragraphs, tables), then pack tree nodes into chunks targeting roughly 512 tokens (hard maximum 800) with 10 to 15 percent overlap, never splitting mid-sentence or crossing top-level section boundaries. Every chunk carries full provenance (doc_id, section_path, page_range, char_span, token_count, content_hash) captured at chunk-creation time. Tables are chunked whole with a generated text-summary companion.

## Alternatives considered

- **Fixed-size sliding window** — splits mid-thought and orphans context from headings; deliberately kept as the eval control arm so every chunking claim is measured against it.

- **Recursive character splitting (a common framework default)** — still structure-blind — a character-count rule doesn't distinguish a heading from a footnote.

- **Semantic/embedding-based chunking** — 2-5x embedding cost at ingestion for gains that published evals show are corpus-dependent; deferred as a Phase 3 experiment.

- **Late chunking / parent-document retrieval** — genuinely promising, but deferred to Phase 2 behind the eval harness since it doubles retrieval bookkeeping and should be justified by measured gains first.

## Consequences

Easier: provenance is guaranteed correct because it is captured at chunk birth, not reverse-engineered later. Harder: structure-aware parsing is more implementation work per document format than naive splitting; the 512-token target is explicitly documented as a tunable, re-derivable per corpus type via the eval harness, not a fixed truth.

## Revisit trigger

Eval results show a corpus-specific better default.
