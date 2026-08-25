# Runbook: DiskSpaceLow

**Fires when:** filesystem usage on a `node-exporter`-scraped host exceeds 80%, sustained 10 minutes (§10.4). `node-exporter` runs as part of the `observability` compose profile and reports the real host it's running on — this alert is live today, not deferred like cert-expiry.

## 1. Confirm and find what's growing

```
docker compose -f infra/compose/compose.yml exec postgres df -h
docker system df -v
```

Or via Prometheus directly:

```
curl -s http://localhost:9091/api/v1/query --data-urlencode \
  'query=node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"}'
```

## 2. Likely culprits, in order

- **Postgres WAL/data growth** — `docker volume inspect aether_pg_data`, then check table/index bloat if genuinely large (`SELECT pg_size_pretty(pg_database_size(current_database()));`).
- **Loki/Tempo/Prometheus retention** — each has a bounded retention (7d for Loki per `infra/loki/config.yaml`, 72h block retention for Tempo per `infra/tempo/config.yaml`) but a long-running dev box can still accumulate volume data across many restarts. `docker volume ls | grep aether`.
- **MinIO object growth** — uploaded documents + generated exports/archives (§DF-3/FR-AD-5's export saga writes real zip archives to object storage).
- **Docker image/build cache bloat** (dev-machine specific, not a prod concern) — `docker system df`.

## 3. Mitigate

- Prune unused Docker resources first (safest, reversible): `docker system prune` (review what it would remove before confirming).
- If a specific volume is the real growth driver, address its retention/cleanup policy rather than just freeing space once (a repeat of this alert next week means the underlying growth wasn't actually fixed).
- At the >80% threshold there's still headroom — this is a page, not a "disk is full" emergency; don't take destructive action under time pressure.

## 4. Verify

```
curl -s http://localhost:9091/api/v1/query --data-urlencode \
  'query=1-(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"}/node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs"})'
```

Should read back under 0.8.

## 5. Postmortem

Only needed if this caused a real service degradation (e.g. Postgres refused writes) — routine disk-growth firings that were mitigated in time don't need one, but note the trend in a ticket regardless (§10.4's ticket-grade cousin).
