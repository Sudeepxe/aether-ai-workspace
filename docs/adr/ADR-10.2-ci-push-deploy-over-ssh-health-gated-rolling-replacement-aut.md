# ADR-10.2: CI-push deploy over SSH, health-gated rolling replacement, automatic rollback on failed smoke test (D10-2)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Without a Kubernetes substrate, the deployment mechanism needed its own decision for how new code reaches the VPS safely, with zero-downtime rolling replacement and a real rollback path.

## Decision

Push-based deploy from CI over SSH: pull new digests, run docker compose up -d with rolling replacement (API replicas replaced one at a time behind Caddy health checks, SSE drain within 60 seconds), migrations run before rollout since expand-contract makes this safe, followed by a post-deploy smoke test (healthz, login, one grounded chat turn, one refusal case) that triggers automatic rollback to the previous, kept-warm digests on failure.

## Alternatives considered

- **Watchtower or other poll-based deployment** — rejected — deploys should be events with authors, logs, and gates, not eventual conditions a poller happens to notice.

- **GitOps (Argo or Flux)** — rejected as the right answer specifically on Kubernetes, but machinery without a substrate here; explicitly deferred to if and when the Kubernetes adoption trigger fires.

- **Blue-green deployment on one box** — rejected, doubles memory footprint on the VPS for marginal gain over health-gated rolling.

- **Canary releases** — rejected as meaningless at demo traffic volume; the post-deploy smoke test effectively serves as the canary.

## Consequences

Easier: every deploy is an authored, logged, gated event with automatic rollback on failure, achieving elite deploy-frequency and lead-time behaviors measurable from git and deploy history. Harder: an early draft pulled images by mutable tag rather than digest, meaning a re-pushed tag could silently change what rollback restores — corrected in the chapter's own self-review to digest-pinned, cosign-verified pulls recorded in an in-repo deploy log.

## Revisit trigger

Kubernetes adoption, at which point deployment moves to GitOps.
