# ADR-10.1: Single VPS with Docker Compose for demo production, prioritizing ops competence over PaaS convenience (D10-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The demo production environment needed a hosting-topology decision that balances managed-platform convenience against demonstrating genuine operations competence, which the project treats as part of its portfolio value.

## Decision

One 4 vCPU, 8GB VPS running the demo compose profile (API times two, worker, Caddy with HTTP/2 and auto-TLS, Postgres, Redis, MinIO, the LGTM observability stack), plus offsite object storage for backups, accepting a single-node failure domain at the demo tier and stating that honestly in public docs.

## Alternatives considered

- **A platform-as-a-service such as Fly.io, Render, or Railway** — genuinely attractive for managed TLS and deploy UX, but rejected because the multi-container topology plus the LGTM observability stack fragments awkwardly across PaaS pricing and units, and because running one's own box with runbooks and drills is itself the operations-competence signal the project wants to demonstrate; acknowledged as the right first answer for a real startup, a different objective than this project's.

- **Managed Kubernetes (EKS or GKE)** — already rejected via ADR-3.9 as operational theater for one demo; the Kubernetes-readiness checklist is verified instead of an actual cluster being run.

- **Serverless** — already rejected via D3-1 for SSE incompatibility and local-first violation.

## Consequences

Easier: demonstrates real operational skill, such as runbooks, drills, and monitoring, that a PaaS abstracts away entirely. Harder: one box genuinely is a single failure domain — accepted at demo tier with a documented recovery point and recovery time objective, and stated publicly rather than glossed over.

## Revisit trigger

Real users appear, at which point managed Postgres and a second node come first.
