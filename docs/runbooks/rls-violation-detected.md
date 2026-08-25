# Runbook: RLSViolationDetected

**Fires when:** a real Postgres `WITH CHECK` rejection (SQLSTATE 42501, "new row violates row-level security policy") reaches the HTTP layer's catch-all handler (§10.4). This should **never** happen in normal operation — forced RLS means every tenant-scoped write is supposed to be correctly tenant-scoped by construction before it ever reaches Postgres. If this fires, either a real bug shipped, or someone is actively probing for a tenant-isolation bypass.

## 1. Treat as a genuine security incident until proven otherwise

This is the one alert in the whole system where "it's probably fine, just noise" is *never* the right first assumption.

## 2. Find the exact request

```
docker compose -f infra/compose/compose.yml logs api | grep unhandled_exception
```

Every occurrence logs `exc_type`, `path`, and `correlation_id` (see `problem_json.py`'s `_unhandled_handler`). Cross-reference the correlation id against Tempo (if tracing is enabled) or the access logs for the caller's identity/workspace context.

## 3. Diagnose

- **A real application bug**: some code path is constructing a query or mutation with the wrong `tenant_id` — reachable *despite* `set_config('app.tenant_id', ...)` correctly scoping the session, meaning the bug is in application logic that passes a mismatched ID somewhere (e.g. cross-referencing a resource that legitimately belongs to a different workspace). Find the exact call site from the stack context around the correlation id.
- **An active bypass attempt**: someone is manipulating request parameters trying to write into another tenant's data. Check whether the same caller/IP has other suspicious activity (repeated 403s, rapid probing across workspace ids) around the same time.

## 4. Mitigate

- If it's a real bug: the RLS policy did its job — the write was blocked, no data actually crossed tenant boundaries. Ship a fix; no data remediation needed since the write never landed.
- If it's an active bypass attempt: the same defense already stopped it. Consider whether the caller's account/API key should be suspended pending investigation.

## 5. Verify

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=increase(aether_rls_violation_total{job="aether-api"}[1h])'
```

## 6. Postmortem

Always required, no exceptions — this is a security-invariant alert, and even a "just a bug, nothing got through" firing needs the timeline documented (§10.4). Use [`postmortem-template.md`](postmortem-template.md).
