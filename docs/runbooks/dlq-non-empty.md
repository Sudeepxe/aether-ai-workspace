# Runbook: OutboxDLQNonEmpty / IngestionDLQNonEmpty

**Fires when:** an outbox row exhausted its 5 dispatch attempts (§3.6.2), or an ingestion message exhausted its Redis Streams delivery attempts, sustained for 15 minutes. Both mean **at-least-once delivery gave up** — the row/message needs a human to look at it; it will not retry itself further.

## 1. Identify what's stuck

**Outbox DLQ** (rows with `attempts >= 5`, `dispatched_at IS NULL` — see the outbox migration's docstring for why this is the DLQ signal, not a separate table):

```sql
-- against the migrator role, read-only
SELECT id, aggregate_type, event_type, tenant_id, attempts, created_at, payload
FROM outbox
WHERE dispatched_at IS NULL AND attempts >= 5
ORDER BY created_at;
```

**Ingestion DLQ** (a real Redis stream, `ingest:dlq`):

```
redis-cli -h localhost XRANGE ingest:dlq - + COUNT 20
```

Each entry carries `tenant_id`, `original_message_id`, and `delivery_count` in its fields (see `RedisIngestionQueue.fail()`).

## 2. Diagnose why dispatch/processing kept failing

```
docker compose -f infra/compose/compose.yml logs worker | grep -E "dispatch_failed|ingestion_dispatch_failed"
```

Common causes:
- **A downstream dependency was down long enough to exhaust every retry** (mailpit/Resend for email; MinIO/ClamAV/embedder for ingestion) — check whether the outage window overlaps the DLQ'd rows' `created_at`.
- **A malformed payload** a handler can never process (a genuine bug, not a transient failure) — the log line's exception detail is the fastest signal.

## 3. Remediate

- **Transient-cause rows**: once the dependency is healthy again, reset `attempts` to 0 (outbox) so the normal poll loop picks it back up:
  ```sql
  UPDATE outbox SET attempts = 0 WHERE id = '<id>';
  ```
  For ingestion, re-`XADD` the DLQ entry's fields back onto its tenant's live stream (drop the DLQ-only fields first: `tenant_id`, `original_message_id`, `delivery_count`).
- **Genuine-bug rows**: fix the bug, ship it, *then* replay — replaying against the same broken code just re-fills the DLQ.
- **Rows that can never succeed** (e.g. a since-deleted workspace): document why in the postmortem and delete/XDEL them — a DLQ isn't a place to accumulate permanent garbage either.

## 4. Verify recovery

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=aether_outbox_dlq_depth'
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=aether_ingestion_dlq_depth{job="aether-worker"}'
```

Both should read 0 (or only rows you've deliberately decided to leave, documented).

## 5. Postmortem

Required if the DLQ'd rows represent real lost/delayed work a tenant would notice (a missed email, a document stuck mid-ingest). Use [`postmortem-template.md`](postmortem-template.md).
