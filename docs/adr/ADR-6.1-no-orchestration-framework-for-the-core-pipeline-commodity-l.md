# ADR-6.1: No orchestration framework for the core pipeline; commodity libraries at the edges only

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The RAG orchestration loop (retrieve, assemble, generate, validate, attribute) is the project's technical thesis — the mechanics are what the project exists to demonstrate mastery of, and existing frameworks such as LangChain or LlamaIndex would hide exactly that logic behind their own abstractions.

## Decision

No orchestration framework is used for the core pipeline. Commodity libraries are used only at the edges (parsers, tokenizers) where they are genuine commodities, not differentiators.

## Alternatives considered

- **LangChain** — abstraction churn (both major frameworks have broken compatibility repeatedly) and it would hide the security-critical prompt-assembly path behind an inherited abstraction rather than an owned one; acknowledged as good for quick prototyping breadth.

- **LlamaIndex** — rejected for core but conceptually borrowed — its parent-document retrieval pattern appears in the Phase 2 roadmap; best-in-class specifically for ingestion abstractions.

- **Semantic Kernel / Haystack** — same rejection reasoning as LangChain; Haystack's pipeline DAG model is noted as closest to what is hand-built here.

- **Vercel AI SDK** — wrong tier — UI-stream oriented, not orchestration.

## Consequences

Easier: the orchestration logic is fully owned and debuggable without five layers of abstraction, and demonstrable in interviews as built, not framework-provided. Harder: solved plumbing (retries, streaming glue) must be re-implemented, mitigated by it being small and already owned by the Chapter 3/4 designs; the project forgoes framework-community velocity on new emerging patterns, mitigated by porting patterns as designs rather than dependencies.

## Revisit trigger

The team scales, or the proof burden shifts from architecture to product velocity.
