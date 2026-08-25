# Runbook: APIDown

**Fires when:** `up{job="aether-api"} == 0` for 2 minutes — Prometheus can't scrape `/metrics` at all, which almost always means the process is down or the container can't reach it.

## 1. Confirm it's real, not a scrape-path problem

```
curl -sf http://localhost:8000/healthz || echo "API not responding"
docker compose -f infra/compose/compose.yml ps api
docker compose -f infra/compose/compose.yml logs --tail=200 api
```

If `healthz` responds but Prometheus still shows `up == 0`, it's a network/scrape-config problem, not an outage — check `infra/prometheus/prometheus.yml`'s `aether-api` target and that the `app` + `observability` compose profiles are on the same Docker network.

## 2. If the process is actually down

```
docker compose -f infra/compose/compose.yml --profile app up -d api
docker compose -f infra/compose/compose.yml logs --tail=200 api
```

Common causes, in likelihood order:
- **DB unreachable** — check `docker compose ps postgres`; the API's `readyz` (not `healthz`) checks the DB pool and will 503/degrade first.
- **Bad deploy** — a fresh digest that fails startup (e.g. a broken migration was applied but the new image expects a further-along schema). Roll back to the previous digest (§10.3's rollback path once S11 lands; locally, `docker compose up -d --build` against the previous commit).
- **OOM/crash loop** — `docker compose logs api | grep -i "killed\|traceback"`.

## 3. Verify recovery

```
curl -sf http://localhost:8000/healthz && curl -sf http://localhost:8000/readyz
```

And confirm the Prometheus target flips back to `up`:

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=up{job="aether-api"}'
```

## 4. Postmortem

Any real page here (not a synthetic/burn-test firing) gets a postmortem using [`postmortem-template.md`](postmortem-template.md) — a real outage, even a self-inflicted one during dev, is worth the five minutes.
