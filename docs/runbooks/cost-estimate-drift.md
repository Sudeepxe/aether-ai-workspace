# Runbook: CostEstimateDriftHigh (ticket-grade)

**Fires when:** admission-time ceiling estimates (`aether_admission_estimated_cost_microcents_total`, §3.2.14) diverge from settled actual cost (`aether_settled_cost_microcents_total`) by more than 5% over a rolling 1h window. Ticket-grade, not page-grade — this is a calibration signal, not an incident; file a ticket, don't wake anyone up.

## 1. Confirm the direction of drift

```
curl -s http://localhost:9091/api/v1/query --data-urlencode \
  'query=(increase(aether_admission_estimated_cost_microcents_total{job="aether-api"}[1h])-increase(aether_settled_cost_microcents_total{job="aether-api"}[1h]))/increase(aether_settled_cost_microcents_total{job="aether-api"}[1h])'
```

Positive = estimates are running *high* (conservative — wastes admission headroom, but never overspends). Negative = estimates are running *low* (the concerning direction — the hybrid admission+settlement design bounds overshoot to `concurrent in-flight × max_tokens`, §3.2.14, but a systematically low estimate erodes that safety margin).

## 2. Diagnose

The admission ceiling estimate is `prompt tokens counted locally + max_tokens`, priced at `AETHER_ADMISSION_CEILING_COST_PER_1K_MICROCENTS` (a configured worst-case rate, see `config.py`'s comment on which provider/model it should track). Drift usually means:
- **The configured worst-case rate is stale** — a provider changed pricing, or the model chain's mix shifted toward a pricier model than the constant assumes.
- **Actual completions are systematically shorter than `max_tokens`** (over-conservative estimate — the "positive drift" case, not urgent) or **longer/more expensive than expected** (under-estimate — check whether `router_max_tokens` itself needs revisiting).

## 3. Mitigate

Update `admission_ceiling_cost_per_1k_microcents` in `config.py` (and `.env.example`) to the real current worst-case rate across the configured model chain — the same recalibration discipline as the retrieval-refusal threshold (`retrieval_refusal_threshold`'s own docstring is the template for how a data-derived constant like this should be documented and revisited).

## 4. Verify

Re-check the same query after a few hours of the new estimate in production; drift should trend back under 5%.

## 5. Postmortem

Not required — this is exactly the kind of self-healing calibration ticket §10.4 describes ("mistakes" section: "coverage worship... treating the PRR as paperwork" — the flip side is genuinely using tickets for genuinely ticket-grade signals, not escalating everything to an incident).
