# ADR-3.9: SSE over WebSocket for streaming; Kubernetes-ready but deliberately not deployed

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Chat token streaming is inherently unidirectional (server to client), which affects load-balancer/proxy compatibility and reconnect semantics. Separately, the deployment substrate needed a decision independent of whether streaming "requires" Kubernetes-style infrastructure.

## Decision

SSE (Server-Sent Events) is used for streaming rather than WebSocket, since the need is unidirectional, SSE is HTTP-native (load-balancer and proxy friendly), and it supports auto-reconnect plus Last-Event-ID resume natively. Separately, the architecture is built to pass a full Kubernetes-readiness checklist (stateless processes, env/secret-mounted config, graceful SIGTERM within 30s including SSE drain, liveness/readiness split, no sticky sessions) without actually running a Kubernetes cluster for the demo.

## Alternatives considered

- **WebSocket** — bidirectional capability is unneeded in the MVP, and WebSocket connections create load-balancer stickiness pressure the design wants to avoid.

- **Long-polling** — worse latency and per-request overhead than SSE for this workload.

- **Running a managed Kubernetes cluster (EKS/GKE) for the demo** — operational theater for serving one demo, per the same reasoning as ADR-3.1's rejection of full microservices.

## Consequences

Easier: SSE resumes cleanly through ordinary HTTP infrastructure, and the pre-verified readiness checklist means Kubernetes adoption later is a repackaging exercise (Deployment plus HPA for the API tier, Deployment plus KEDA for workers), not a redesign. Harder: SSE's unidirectional nature means any future client-to-server mid-stream control beyond cancel (which already works via a separate DELETE call) would need a transport rethink.

## Revisit trigger

Phase 2 agents need client-to-server mid-stream control beyond cancel.
