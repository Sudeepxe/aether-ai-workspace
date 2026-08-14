# ADR-3.5: Thin in-house LLM router, with LiteLLM acknowledged as the company-setting default

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Aether needs one internal interface over multiple LLM providers (OpenAI, Anthropic, Ollama) with capability-flag routing, fallback chains, and budget integration — a build-vs-buy decision against LiteLLM, which already solves most of this.

## Decision

Build a thin in-house router rather than adopt LiteLLM, because (a) it is a primary learning/demonstration artifact of the project, (b) capability-flag routing and budget integration are custom anyway, and (c) LiteLLM's dependency churn rate is high.

## Alternatives considered

- **LiteLLM** — solves roughly 80% of the router's responsibilities and is explicitly acknowledged as the defensible default choice in a company setting; rejected here specifically because building it is part of the project's demonstration value, not because it is technically inferior.

## Consequences

Easier: full control over capability-flag routing and budget integration, with no dependency-churn exposure. Harder: re-implements solved plumbing (retries, provider adapters, health probes) that LiteLLM would otherwise provide — accepted as the cost of the demonstration goal.

## Revisit trigger

Maintenance burden exceeds learning value.
