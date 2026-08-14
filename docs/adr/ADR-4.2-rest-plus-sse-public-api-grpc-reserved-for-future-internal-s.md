# ADR-4.2: REST plus SSE public API; gRPC reserved for future internal seams; GraphQL declined (D4-2)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The API needs a paradigm decision serving one first-party consumer (the SPA) and one external persona (Devon) who expects REST and OpenAPI, given the resources (workspaces, threads, documents) are genuinely resource-shaped and streaming is unidirectional.

## Decision

REST (resource-oriented) plus SSE for streaming. gRPC is reserved as the pre-selected contract for any future internal service-to-service extraction; GraphQL is declined for the public API.

## Alternatives considered

- **GraphQL** — solves client-driven aggregation across many resources and teams, a problem this system does not have (one backend, one SPA, shallow view models); would impose per-field authorization complexity on top of RLS, query-cost analysis, cache fragmentation, and a worse story for the external API persona.

- **gRPC as the primary public API now** — deferred rather than adopted now — excellent for internal contracts that don't yet exist, but browser support requires grpc-web proxying and external-developer ergonomics are worse.

- **tRPC** — TypeScript-only end-to-end typing couples the API to the frontend stack and excludes the external persona.

## Consequences

Easier: REST's alignment with HTTP semantics gives rate limiting, caching, authorization, and observability per-route for free; a small number of purpose-built read endpoints keep the surface intentional. Harder: REST's under-fetching is real — mitigated by explicit response schemas and a `:verb`-suffix convention for non-CRUD custom actions.

## Revisit trigger

Third-party aggregation demand materializes.
