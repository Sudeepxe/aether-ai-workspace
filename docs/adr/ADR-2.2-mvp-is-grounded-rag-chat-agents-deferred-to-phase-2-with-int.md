# ADR-2.2: MVP is grounded RAG chat; agents deferred to Phase 2 with interfaces designed in Ch. 6 (D2-2)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Chapter 2 had to decide what the platform *is* for its first phase, choosing between an agents-first MVP, building everything in parallel, a chat-only MVP, or RAG-as-a-library.

## Decision

RAG-grounded chat is the MVP. It exercises the entire platform spine (async ingestion, queueing, vector storage, prompt assembly, streaming, citations, evals, cost metering) and produces the project's North Star metric. Agents are deferred to Phase 2, but the tool-calling interface is designed now so Phase 2 is additive, not disruptive.

## Alternatives considered

- **Agents-first MVP** — agents without grounding are demos, not products; tool execution is the largest attack surface (per the anti-persona table) and has no mature platform underneath it yet; also unevaluable — agent quality has no metric analogous to faithfulness/refusal.

- **Everything in parallel** — for one engineer this maximizes work-in-progress and integration risk, and eliminates the intermediate proof points a portfolio needs.

- **Chat-only MVP (no RAG)** — fails the differentiation test — a multi-tenant chat wrapper is precisely the "student project" failure mode the project exists to avoid.

- **RAG-as-a-library (skip the platform)** — demonstrates AI engineering but abandons the backend/system-design portfolio goals; would be the right call only if the goal were an OSS library rather than a platform.

## Consequences

Easier: forces every production system (auth, tenancy, observability, CI) to exist before any AI feature ships, and produces the headline North Star evidence. Harder: the differentiating agent feature is delayed a full phase, and the platform risks looking like "another RAG chat" until Phase 2 lands — mitigated by freezing the tool-calling interface now.

## Revisit trigger

Low — phase exit criteria guard it.
