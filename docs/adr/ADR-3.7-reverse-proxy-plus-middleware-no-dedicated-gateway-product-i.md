# ADR-3.7: Reverse proxy plus middleware, no dedicated gateway product in v1

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The API Gateway responsibility (TLS termination, request-ID injection, coarse rate limiting, routing) needs an implementation choice between a dedicated API gateway product and a simpler reverse-proxy-plus-middleware split.

## Decision

v1 uses a reverse proxy (Caddy/Traefik) for edge concerns plus in-app middleware for authentication, tenant-context binding, fine-grained rate limits, and budget admission — not a dedicated gateway product such as Kong or Envoy Gateway.

## Alternatives considered

- **Dedicated gateway product (Kong, Envoy Gateway)** — earns its keep with many services and teams; with one API service it is an extra hop and configuration surface for no benefit.

## Consequences

Easier: fewer moving parts and less operational surface, with no gateway-product configuration to maintain. Harder: some gateway-product conveniences (centralized plugin ecosystems) are foregone — acceptable at this service count.

## Revisit trigger

Multi-service extraction begins.
