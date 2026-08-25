# Runbook: SLOBurnRateHigh

**Fires when:** the 5xx ratio exceeds 2% over the trailing 1h **and** 5% over the trailing 6h simultaneously (§10.4) — the two-window check means a brief blip won't page, but a real sustained error surge will.

## 1. Find what's actually failing

Open the [SLO Overview dashboard](http://localhost:3000/d/aether-slo-overview) (5xx error rate + request rate by route panels), or query directly:

```
curl -s http://localhost:9091/api/v1/query --data-urlencode \
  'query=sum(rate(aether_http_request_duration_seconds_count{status=~"5.."}[5m])) by (route)'
```

Whichever route dominates the 5xx count is where to start reading logs:

```
docker compose -f infra/compose/compose.yml logs --tail=500 api | grep '"level":"error"'
```

Or in Grafana's Explore view against Loki: `{container="aether-api"} | json | level="error"`.

## 2. Common root causes

- **A dependency is down** (Postgres/Redis/MinIO/ClamAV/a provider) — `readyz` degrades, and every request touching that dependency 500s. Check `docker compose ps` for the unhealthy service first.
- **A bad deploy** introduced a real bug — correlate the burn-rate start time against the last deploy timestamp (deploy log, once S11's pipeline lands).
- **Provider outage cascading past the router's fallback chain** — `NoProviderAvailableError` maps to 503, not the same code path but worth checking the [AI-plane dashboard](http://localhost:3000/d/aether-ai-plane)'s fallback-rate panel.

## 3. Mitigate

- If it's a bad deploy: roll back (§10.3 — once S11's pipeline exists; locally, redeploy the previous commit's image).
- If it's a dependency outage: the platform's own degraded-mode behaviors (§3.2.12, verified by the chaos-lite suite, #97) should already be containing blast radius — confirm they're actually engaged (e.g., rate limiting fell back to local buckets, not also erroring).

## 4. Verify recovery

Burn rate is a rate over a window, so it won't clear instantly — watch both windows trend back down:

```
curl -s http://localhost:9091/api/v1/query --data-urlencode \
  'query=sum(rate(aether_http_request_duration_seconds_count{status=~"5.."}[1h]))/sum(rate(aether_http_request_duration_seconds_count[1h]))'
```

## 5. Postmortem

Required — this alert firing means real user-facing errors happened. Use [`postmortem-template.md`](postmortem-template.md).
