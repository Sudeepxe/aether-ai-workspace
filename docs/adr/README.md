# Architecture Decision Records

Template: `0000-adr-template.md` (MADR-lite; Blueprint §9.2). ADRs are
immutable once Accepted — superseding requires a new ADR linking back.

**Status:** all 56 ADRs referenced by `docs/architecture/blueprint.md` (ADR-2.1
through ADR-11.3) have been extracted into individual numbered files below,
generated from the blueprint's per-chapter decision records and self-review
findings (Sprint 0, SPRINT_0_PLAN days 1-2). The blueprint's per-chapter ADR
tables remain the canonical summary; these files carry the full MADR-lite detail
(Context / Decision / Alternatives / Consequences / Revisit trigger).

## Index

### Chapter 2 — Requirements & Feature Prioritization

- [ADR-2.1](ADR-2.1-moscow-prioritization-for-v1-d2-1.md) — MoSCoW prioritization for v1 (D2-1)
- [ADR-2.2](ADR-2.2-mvp-is-grounded-rag-chat-agents-deferred-to-phase-2-with-int.md) — MVP is grounded RAG chat; agents deferred to Phase 2 with interfaces designed in Ch. 6 (D2-2)
- [ADR-2.3](ADR-2.3-vector-store-is-derived-data-source-of-truth-is-object-stora.md) — Vector store is derived data; source of truth is object storage plus Postgres
- [ADR-2.4](ADR-2.4-nfrs-enforced-as-ci-budgets-and-prod-monitors-relative-regre.md) — NFRs enforced as CI budgets and prod monitors, relative-regression thresholds in CI (D2-3)

### Chapter 3 — System Architecture

- [ADR-3.1](ADR-3.1-modular-monolith-plus-async-workers-kubernetes-ready-not-dis.md) — Modular monolith plus async workers; Kubernetes-ready, not distributed (D3-1)
- [ADR-3.2](ADR-3.2-pgvector-in-postgres-with-a-qdrant-escape-hatch-on-pre-commi.md) — pgvector in Postgres, with a Qdrant escape hatch on pre-committed triggers (D3-2)
- [ADR-3.3](ADR-3.3-redis-streams-transport-plus-a-postgres-transactional-outbox.md) — Redis Streams transport plus a Postgres transactional outbox, CloudEvents-compatible envelope (D3-3)
- [ADR-3.4](ADR-3.4-hexagonal-architecture-with-lint-enforced-import-direction.md) — Hexagonal architecture with lint-enforced import direction
- [ADR-3.5](ADR-3.5-thin-in-house-llm-router-with-litellm-acknowledged-as-the-co.md) — Thin in-house LLM router, with LiteLLM acknowledged as the company-setting default
- [ADR-3.6](ADR-3.6-redis-outage-policy-fail-open-on-jwt-validity-with-a-bounded.md) — Redis-outage policy: fail open on JWT validity with a bounded 15-minute exposure window
- [ADR-3.7](ADR-3.7-reverse-proxy-plus-middleware-no-dedicated-gateway-product-i.md) — Reverse proxy plus middleware, no dedicated gateway product in v1
- [ADR-3.8](ADR-3.8-presigned-urls-file-bytes-never-transit-the-api.md) — Presigned URLs; file bytes never transit the API
- [ADR-3.9](ADR-3.9-sse-over-websocket-for-streaming-kubernetes-ready-but-delibe.md) — SSE over WebSocket for streaming; Kubernetes-ready but deliberately not deployed

### Chapter 4 — Backend & API Design

- [ADR-4.1](ADR-4.1-python-3.12-with-fastapi-and-pydantic-v2-strict-mypy-no-bloc.md) — Python 3.12 with FastAPI and Pydantic v2, strict mypy, no blocking I/O on the loop (D4-1)
- [ADR-4.2](ADR-4.2-rest-plus-sse-public-api-grpc-reserved-for-future-internal-s.md) — REST plus SSE public API; gRPC reserved for future internal seams; GraphQL declined (D4-2)
- [ADR-4.3](ADR-4.3-uuidv7-for-all-client-visible-ids.md) — UUIDv7 for all client-visible IDs
- [ADR-4.4](ADR-4.4-cursorkeyset-pagination-only-offset-banned-by-lint-on-new-en.md) — Cursor/keyset pagination only; offset banned by lint on new endpoints
- [ADR-4.5](ADR-4.5-deny-by-default-route-registration-undeclared-authorization.md) — Deny-by-default route registration: undeclared authorization is a boot failure
- [ADR-4.6](ADR-4.6-idempotency-key-with-body-hash-and-24-hour-response-replay-c.md) — Idempotency-Key with body-hash and 24-hour response replay; client message_id for chat
- [ADR-4.7](ADR-4.7-url-path-versioning-additive-only-within-a-version-6-month-s.md) — URL-path versioning, additive-only within a version, 6-month Sunset on breaking changes

### Chapter 5 — Frontend Design

- [ADR-5.1](ADR-5.1-react-typescript-and-vite-as-a-pure-spa-with-no-ssr-250kb-gz.md) — React, TypeScript, and Vite as a pure SPA with no SSR; 250KB gzipped initial budget (D5-1)
- [ADR-5.2](ADR-5.2-tanstack-query-for-server-state-zustand-for-client-state-str.md) — TanStack Query for server state, Zustand for client state; streaming bypasses the cache with an rAF-batched buffer (D5-2)
- [ADR-5.3](ADR-5.3-fetch-plus-readablestream-sse-client-since-native-eventsourc.md) — fetch plus ReadableStream SSE client, since native EventSource is unusable (GET-only, no auth header)
- [ADR-5.4](ADR-5.4-radix-plus-tailwind-headless-component-approach.md) — Radix plus Tailwind headless component approach
- [ADR-5.5](ADR-5.5-no-offline-cache-of-tenant-content-prioritizing-security-ove.md) — No offline cache of tenant content, prioritizing security over convenience

### Chapter 6 — AI Architecture & RAG

- [ADR-6.1](ADR-6.1-no-orchestration-framework-for-the-core-pipeline-commodity-l.md) — No orchestration framework for the core pipeline; commodity libraries at the edges only
- [ADR-6.2](ADR-6.2-structure-aware-chunking-at-roughly-512800-tokens-with-prove.md) — Structure-aware chunking at roughly 512/800 tokens with provenance captured at birth; fixed-size kept as an eval control
- [ADR-6.3](ADR-6.3-hybrid-rrf-retrieval-for-mvp-reranking-gated-on-a-5-point-ev.md) — Hybrid RRF retrieval for MVP; reranking gated on a 5-point eval gain; condensing rewrite with dual-feed to lexical and vector legs
- [ADR-6.4](ADR-6.4-two-gate-refusal-a-calibrated-retrieval-threshold-plus-a-gen.md) — Two-gate refusal: a calibrated retrieval threshold plus a generation protocol
- [ADR-6.5](ADR-6.5-judge-from-a-different-model-family-than-the-generator-human.md) — Judge from a different model family than the generator, human-calibrated, tiered eval spend, fail-closed merges on AI paths
- [ADR-6.6](ADR-6.6-tool-calls-are-proposals-through-a-policy-engine-injection-f.md) — Tool calls are proposals through a policy engine; injection-flagged context escalates approval

### Chapter 7 — Authentication, Authorization & Security

- [ADR-7.1](ADR-7.1-hybrid-session-httponly-path-scoped-__host--refresh-cookie-p.md) — Hybrid session: httpOnly path-scoped __Host- refresh cookie plus in-memory bearer access token (D7-1); BFF as the Phase 3 enterprise path
- [ADR-7.2](ADR-7.2-eddsa-pinned-15-minute-jwt-with-rotating-hashed-refresh-toke.md) — EdDSA-pinned 15-minute JWT with rotating hashed refresh tokens, family reuse-detection, and a 30-second same-device grace window (D7-2)
- [ADR-7.3](ADR-7.3-oauth-verified-email-gate-with-explicit-account-linking-pkce.md) — OAuth verified-email gate with explicit account linking; PKCE mandatory
- [ADR-7.4](ADR-7.4-authorization-matrix-tests-generated-from-the-policy-map-so.md) — Authorization-matrix tests generated from the policy map, so the matrix cannot drift from code
- [ADR-7.5](ADR-7.5-sops-and-age-for-in-repo-secrets-with-envelope-encryption-fo.md) — SOPS and age for in-repo secrets, with envelope encryption for stored provider keys
- [ADR-7.6](ADR-7.6-multi-factor-authentication-deferred-to-phase-3-recorded-as.md) — Multi-factor authentication deferred to Phase 3, recorded as a known, stated gap

### Chapter 8 — Database Design

- [ADR-8.1](ADR-8.1-shared-tables-with-forced-row-level-security-and-three-disti.md) — Shared tables with forced row-level security and three distinct database roles (D8-1)
- [ADR-8.2](ADR-8.2-messages-stored-as-rows-with-a-transactional-per-thread-seq.md) — Messages stored as rows with a transactional per-thread seq (D8-2, resolves OQ-3.4)
- [ADR-8.3](ADR-8.3-monthly-range-partitions-for-usage-and-audit-tables-only-ret.md) — Monthly range partitions for usage and audit tables only; retention enforced by partition drop (D8-3)
- [ADR-8.4](ADR-8.4-halfvec-vector-storage-adopted-pending-recall-verification-o.md) — halfvec vector storage adopted pending recall verification on the golden set
- [ADR-8.5](ADR-8.5-no-production-down-migrations-expand-contract-only-with-an-n.md) — No production down-migrations; expand-contract only, with an N-1 compatibility CI gate
- [ADR-8.6](ADR-8.6-citations-denormalize-a-provenance-snapshot-rather-than-rely.md) — Citations denormalize a provenance snapshot rather than relying on a live chunk foreign key

### Chapter 9 — Repository Structure, Documentation & DX

- [ADR-9.1](ADR-9.1-single-monorepo-no-monorepo-build-graph-tooling-layer-d9-1.md) — Single monorepo; no monorepo build-graph tooling layer (D9-1)
- [ADR-9.2](ADR-9.2-contracts-are-generated-artifacts-only-hand-edited-contracts.md) — Contracts are generated artifacts only; hand-edited contracts are banned
- [ADR-9.3](ADR-9.3-the-module-tree-mirrors-the-3.2-service-catalog-one-to-one.md) — The module tree mirrors the §3.2 service catalog one-to-one, so architecture is visible in ls
- [ADR-9.4](ADR-9.4-solo-pull-request-discipline-with-required-checks-pr-history.md) — Solo pull-request discipline with required checks; PR history treated as portfolio evidence
- [ADR-9.5](ADR-9.5-apache-2.0-license-synthetic-only-eval-corpora-for-licensing.md) — Apache-2.0 license; synthetic-only eval corpora for licensing and privacy

### Chapter 10 — Deployment, DevOps, CI/CD & Production Readiness

- [ADR-10.1](ADR-10.1-single-vps-with-docker-compose-for-demo-production-prioritiz.md) — Single VPS with Docker Compose for demo production, prioritizing ops competence over PaaS convenience (D10-1)
- [ADR-10.2](ADR-10.2-ci-push-deploy-over-ssh-health-gated-rolling-replacement-aut.md) — CI-push deploy over SSH, health-gated rolling replacement, automatic rollback on failed smoke test (D10-2)
- [ADR-10.3](ADR-10.3-ephemeral-staging-built-from-the-restore-drill-output-one-me.md) — Ephemeral staging built from the restore-drill output, one mechanism serving two duties
- [ADR-10.4](ADR-10.4-symptom-based-paging-with-named-runbooks-blameless-postmorte.md) — Symptom-based paging with named runbooks; blameless postmortems published in-repo
- [ADR-10.5](ADR-10.5-the-v1.0-release-gate-is-a-production-readiness-review-check.md) — The v1.0 release gate is a Production Readiness Review checklist, including the eval North Star and a rehearsed rollback

### Chapter 11 — Final Review Package

- [ADR-11.1](ADR-11.1-add-an-email-subsystem-and-a-fully-specified-password-reset.md) — Add an email subsystem and a fully specified password-reset flow (gap remediation)
- [ADR-11.2](ADR-11.2-the-search-endpoints-relevance-scores-are-an-unstable-versio.md) — The search endpoint's relevance scores are an unstable, versioned contract
- [ADR-11.3](ADR-11.3-availability-slo-honesty-split-99.0-percent-for-the-demo-tie.md) — Availability SLO honesty split: 99.0 percent for the demo tier, 99.5 percent as the design SLO on the HA profile

**Total: 56 ADRs.**
