# Runbook: OutboxLagHigh

**Fires when:** the oldest undispatched outbox row (for some `event_type`) is older than 5 minutes, sustained for 5 minutes (§10.4). Unlike the DLQ alert, this fires *before* attempts are exhausted — it means the worker's poll loop isn't keeping up, not that dispatch is impossible.

## 1. Confirm the worker is actually polling

```
docker compose -f infra/compose/compose.yml ps worker
docker compose -f infra/compose/compose.yml logs --tail=100 worker | grep -E "dispatch_cycle|worker_started"
```

If there's no `worker_started` log or no recent dispatch-cycle lines, the process itself is down or wedged — restart it and treat as a variant of [`api-down.md`](api-down.md)'s triage.

## 2. If the worker is running but falling behind

Check which event type is backed up and by how much:

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=aether_outbox_lag_seconds'
```

- **A burst of volume** (e.g. many invitations sent at once) — the poll loop's `batch_size` (20 per cycle, per event type) may just need a cycle or two more; watch the lag trend, don't page-react to a single reading if it's already dropping.
- **A downstream dependency is slow, not down** (SMTP/Resend timing out slowly rather than erroring fast) — each slow call blocks that event type's batch for the whole cycle. Check the dependency's own health/latency.
- **The worker process itself is CPU/IO-starved** — check `docker stats worker`.

## 3. Mitigate

- If it's a genuine backlog with a healthy worker: it will self-clear once volume/dependency latency normalizes — watch, don't intervene.
- If the worker is wedged (running but not making progress): restart it — outbox dispatch is idempotent by construction (every dispatcher's own docstring explains why), so a restart mid-cycle never double-sends or loses work.

## 4. Verify recovery

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=aether_outbox_lag_seconds' 
```

Should trend back toward 0 (or read `no data`, which the gauge treats as "nothing pending" — see `_record_outbox_gauges`'s handling of `oldest_pending_seconds is None`).

## 5. Postmortem

Only needed if this escalated into real user-visible delay (e.g. an invitation email genuinely arrived late) — most firings of this alert are the system doing exactly what it's designed to do (queue under load) and self-resolve.
