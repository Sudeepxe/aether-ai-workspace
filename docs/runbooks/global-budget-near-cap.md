# Runbook: GlobalBudgetNearCap

**Fires when:** cumulative settled spend reaches 90% of the global monthly cap (`AETHER_GLOBAL_MONTHLY_BUDGET_MICROCENTS`, NFR-C-1's $50 demo default), sustained 5 minutes.

## 1. This is an early warning, not an outage

At 90%, the hard kill switch (100%, enforced at the Router — `PostgresBudgetAdmission.check_global()`, a 429 `budget_exhausted` *before* any provider call) hasn't tripped yet. Nothing is broken; this alert exists so a human decides whether to raise the cap *before* real users start seeing 429s.

## 2. Confirm and see who's driving it

Open the [Cost dashboard](http://localhost:3000/d/aether-cost) — "Global spend vs cap" gauge and "Settled spend rate" panel. Then break down by workspace:

```sql
SELECT workspace_id, sum(cost_microcents) AS spend
FROM usage_events
WHERE occurred_at >= date_trunc('month', now())
GROUP BY workspace_id
ORDER BY spend DESC
LIMIT 10;
```

## 3. Decide

- **Expected growth** (more real usage, demo traffic ramping) — this is the system working as designed; either raise `AETHER_GLOBAL_MONTHLY_BUDGET_MICROCENTS` (a deliberate, reviewed decision, not an automatic bump) or let the 100% kill switch engage as the backstop it's meant to be.
- **One workspace burning disproportionately** — check whether its own per-workspace budget (`AETHER_DEFAULT_WORKSPACE_MONTHLY_BUDGET_MICROCENTS`, $5/mo default) is misconfigured too high, or whether this looks like abuse (denial-of-wallet, §1.10) rather than legitimate use.
- **Cost estimate drift** — cross-check the [Cost dashboard](http://localhost:3000/d/aether-cost)'s drift panel; if admission-time estimates have been systematically running low, real spend could be outpacing what the admission checks projected. See [`cost-estimate-drift.md`](cost-estimate-drift.md).

## 4. Mitigate

- Raise the cap deliberately (env var change + redeploy), if justified.
- Tighten a specific workspace's budget if it's the outlier.
- Do nothing and let the 100% hard stop engage — a legitimate, designed outcome for the demo tier's cost posture, not a failure.

## 5. Verify

```
curl -s http://localhost:9091/api/v1/query --data-urlencode \
  'query=aether_global_spend_microcents{job="aether-worker"}/aether_global_budget_cap_microcents{job="aether-worker"}'
```

## 6. Postmortem

Only needed if the cap was actually exhausted and real users saw 429s unexpectedly — a deliberate, planned cap-raise or a clean self-resolve doesn't need one.
