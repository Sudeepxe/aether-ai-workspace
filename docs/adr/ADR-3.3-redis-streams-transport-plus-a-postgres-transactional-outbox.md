# ADR-3.3: Redis Streams transport plus a Postgres transactional outbox, CloudEvents-compatible envelope (D3-3)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Async work (ingestion, housekeeping, deletion cascades) needs a queue/event transport, but some events — usage/billing, deletion, audit-relevant — must never be lost even across a transport failure.

## Decision

Redis Streams (consumer groups) as transport, plus a PostgreSQL transactional outbox for events that must never be lost. The application writes a state change and an outbox row in one ACID transaction; a dispatcher relays outbox rows to the stream; consumers are idempotent, yielding effectively-once processing on top of at-least-once delivery.

## Alternatives considered

- **Kafka** — operational heft (brokers, partitions, rebalancing) is unjustifiable at 60 docs/min; it is the right answer at 100x scale, and the event envelope is designed to port to it.

- **RabbitMQ** — adds a fourth stateful system when Redis is already present; fewer moving parts wins.

- **Postgres-only queue (FOR UPDATE SKIP LOCKED)** — the boring-tech runner-up, genuinely viable at this scale; rejected as the primary transport for weaker fan-out/consumer-group ergonomics, but adopted as the outbox mechanism itself.

- **NATS JetStream** — attractive and light, but rejected on ecosystem maturity for this stack.

## Consequences

Easier: critical events survive Redis loss entirely (rebuildable from the outbox); the dispatcher can run on every worker replica with no leader election, since FOR UPDATE SKIP LOCKED batch-claiming makes concurrent dispatch safe. Harder: non-critical events (status updates) tolerate loss if Redis is lost — an explicit, accepted trade, not an oversight.

## Revisit trigger

Kafka becomes justified at 100x current event volume.
