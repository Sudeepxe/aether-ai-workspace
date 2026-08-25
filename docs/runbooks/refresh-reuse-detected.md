# Runbook: AuthRefreshReuseDetected

**Fires when:** `RefreshSession` detects a used refresh token presented again outside the grace window or from a different device (§7.6, ADR-7.2) — the theft-signal path. This is a security event, not a reliability one: treat every real firing as a possible account compromise until proven otherwise, not noise.

## 1. This has *already* self-remediated the immediate risk

By the time the alert fires, `RefreshSession.execute()` has already called `revoke_family()` — the entire refresh-token family is dead, so the stolen token (and the legitimate one it was cloned from) can no longer mint new access tokens. There is no "stop the bleeding" step; that already happened synchronously in the request path.

## 2. Investigate

```sql
-- Correlate via audit_events for the affected user (the request that
-- triggered the reuse detection carries a correlation id in its logs)
SELECT * FROM audit_events
WHERE event_type LIKE 'auth.%'
ORDER BY occurred_at DESC
LIMIT 20;
```

Pull the correlation id from the alert's time window in the API's logs (or Tempo, if tracing is enabled) to find the exact request — its source IP and device fingerprint are the starting point for "is this the legitimate user on a new device that raced a token refresh, or an actual attacker."

## 3. Distinguish false-positive from real compromise

- **Benign race** (multi-tab double-refresh) is already handled *without* reaching this alert — that's the 30s/same-device grace window (Ch.5 F-1). Reaching this alert means the reuse was outside that window or from a different device fingerprint — a real signal, not routine noise.
- **Legitimate device change** (user got a new phone, old session token leaked into a state where it got replayed) — the user will simply need to log in again; no further action.
- **Actual credential/token theft** — the user should be notified and prompted to re-authenticate everywhere (their next login already requires fresh credentials, since the whole token family is revoked).

## 4. Verify

```
curl -s http://localhost:9091/api/v1/query --data-urlencode 'query=increase(aether_auth_refresh_reuse_total[1h])'
```

A single isolated event, investigated and explained, is not itself an incident. A *pattern* (multiple users, or repeated events for one user) is.

## 5. Postmortem

Required for any firing that isn't conclusively explained as a benign device-change — security events get documented even when they turn out fine, per §10.4's "an incident honestly documented is portfolio gold" framing. Use [`postmortem-template.md`](postmortem-template.md).
