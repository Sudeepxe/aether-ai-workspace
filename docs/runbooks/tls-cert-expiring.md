# Runbook: TLSCertExpiringSoon

**Fires when:** `probe_ssl_earliest_cert_expiry - time() < 14 days` on a `blackbox_exporter`-probed endpoint (§10.4).

**Honest status note:** this alert rule is real (`infra/prometheus/rules/aether.yml`) but has **no live target yet** — there's no `blackbox_exporter` service in the `observability` compose profile, because there's no real TLS endpoint to probe until S11 stands up the `demo` compose profile's Caddy reverse proxy with auto-TLS (D10-1). The rule exists now so it's provably ready and tested (`promtool check rules` passes); it activates for real once S11 lands. This is a documented gap, not a faked one.

## When it does fire (post-S11)

## 1. Confirm the actual cert state

```
echo | openssl s_client -servername <domain> -connect <domain>:443 2>/dev/null | openssl x509 -noout -enddate
```

## 2. Diagnose why auto-renewal didn't happen

Caddy's auto-TLS (Let's Encrypt via ACME) renews automatically well before expiry under normal operation — reaching 14 days out means renewal is *failing*, not just due. Check:

```
docker compose -f infra/compose/compose.yml --profile demo logs caddy | grep -i "acme\|renew\|error"
```

Common causes: DNS no longer resolving to this host (so ACME's HTTP-01 challenge fails), rate-limiting from too many recent renewal attempts, or Caddy's data volume (where it caches certs/account state) got wiped.

## 3. Mitigate

- Fix the underlying ACME failure (DNS, connectivity) and let Caddy retry automatically.
- If urgent and auto-renewal can't be fixed in time, obtain a manual cert and mount it as a Caddy static TLS config (temporary, tracked as follow-up work to restore auto-TLS).

## 4. Verify

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=probe_ssl_earliest_cert_expiry - time()'
```

Should read well above 14 days' worth of seconds (`1209600`).

## 5. Postmortem

Required — a near-miss on cert expiry (however it was caught) means the auto-renewal path itself needs a fix, not just a one-time manual renewal.
