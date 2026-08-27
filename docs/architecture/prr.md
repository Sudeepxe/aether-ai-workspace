# Production Readiness Review (v1.0 release gate)

Gate defined by ADR-10.5 / §10.6: release to v1.0 blocks unless every
line below is green. This document is the PRR *artifact* — walked
line by line, each verdict backed by a real, linkable piece of
evidence (a CI run, a test file, a real drill), never asserted without
one. Per ADR-10.5's own consequence: "assertions without demonstration
don't satisfy the gate."

**Status: v1.0 tagged with one open item, by explicit owner decision.**
7 of 9 checklist lines are ready with real, linked evidence; line #2
(runbook drills) is genuinely partial (3 of 11 fully drilled); line #5
(North Star eval) is a hard blocker — no real LLM provider key exists
in this environment, so faithfulness has never been measured. Rather
than hold the release indefinitely on an external credential this
project has no path to obtain on its own, the owner explicitly chose
to tag v1.0 now with that gap named plainly (S12, 2026-08-27) —
consistent with ADR-10.5's own framing: *"the gap register is a PRR
output, not a confession."* This is not a claim that PRR is fully
green; it is a documented, deliberate release-with-known-gaps
decision. Re-run and update this document (and re-score the North Star
line for real) the moment a provider key becomes available.

## Checklist

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | SLOs defined, dashboarded, and alerted | ✅ Ready | Prometheus rules ([`infra/prometheus/rules/aether.yml`](../../infra/prometheus/rules/aether.yml), 11 alerts incl. `SLOBurnRateHigh`), Grafana dashboards-as-code ([`infra/grafana/dashboards/`](../../infra/grafana/dashboards/)), Alertmanager routing ([`infra/alertmanager/`](../../infra/alertmanager/)). Rule syntax + a subset of alerts synthetically fire-tested via `promtool test rules` in CI's `lint` job (S9 #96). |
| 2 | Runbooks exist and each was executed once for real | ⚠️ **Partial** — see gap register | 11 named runbooks under [`docs/runbooks/`](../runbooks/), one per page-grade alert (§10.4's requirement). Three now have a genuine, real, evidenced drill: [`disaster-recovery.md`](../runbooks/disaster-recovery.md) (S11 #118, real backup/restore/RLS-assertion/vector-rebuild), [`secrets-rotation.md`](../runbooks/secrets-rotation.md) (S10 #109, real SOPS/age key rotation + JWT kid overlap), and [`api-down.md`](../runbooks/api-down.md) (S12 #127 — a real `docker compose kill api`, a real `APIDown` alert firing in Alertmanager after the genuine 2-minute sustained window, real recovery via the runbook's own documented command, real alert-clear; this drill also caught the runbook's own commands missing `--profile dev`, a real bug in the doc itself, now fixed). Three more alerts' *firing* is synthetically proven (`promtool test rules`, [`infra/prometheus/tests/aether_test.yml`](../../infra/prometheus/tests/aether_test.yml)): `AuthRefreshReuseDetected`, `GlobalBudgetNearCap`, `OutboxLagHigh`. Three alerts' underlying degraded-mode *mechanism* is proven by real container-kill chaos experiments (S9 #97, nightly — [`chaos-nightly.yml`](../../.github/workflows/chaos-nightly.yml)): Redis kill (§3.2.12 degraded modes), provider mid-stream kill (SD-1 fallback), worker mid-ingest kill (resume). **Gap:** the remaining runbooks (`outbox-lag-high.md`, `dlq-non-empty.md`, `cost-estimate-drift.md`, `disk-space-low.md`, `refresh-reuse-detected.md`, `rls-violation-detected.md`, `slo-burn-rate.md`, `tls-cert-expiring.md`) have their underlying alert condition or detection logic tested, but the runbook *document's own step-by-step procedure* has not itself been walked through in a live drill — ADR-10.5's literal bar ("executed for real at least once") is not yet met for most of them. `rls-violation-detected.md` specifically is hard to drill honestly: provoking a genuine SQLSTATE 42501 through the live HTTP surface would mean deliberately engineering a code-level bypass bug, which isn't a realistic incident rehearsal — its underlying protection stays proven by the cross-tenant red-team suite and the restore drill's RLS assertion instead. Named as a real, tracked gap, not silently counted as done. |
| 3 | Restore drill passed within RPO/RTO | ✅ Ready | S11 #118 — [`apps/api/scripts/verify_restore_drill.py`](../../apps/api/scripts/verify_restore_drill.py), real two-instance Postgres backup/restore, RLS assertion, vector rebuild, real e2e check. Quarterly + `workflow_dispatch` ([`restore-drill.yml`](../../.github/workflows/restore-drill.yml)). Confirmed green on a real run against `main`. |
| 4 | Security gates green incl. red-team suite | ✅ Ready | Every PR: gitleaks, pip-audit, npm audit, trivy (CRITICAL), ZAP baseline DAST (S10 #108), schemathesis OpenAPI conformance (S10 #107), cross-tenant authz red-team matrix (`security` pytest marker, generated per §7.3). All required, no override path. Current `main` green. |
| 5 | North Star eval ≥ 90/90 on release candidate | ❌ **Blocked** | No real LLM provider API key exists in this environment — an honest, repeatedly-documented gap (not fabricated at any point this project). The eval harness, golden set, and CI gating (smoke on PR, full nightly, release-gated) are all built and wired (S7); what's missing is a real credential to run generation against a real model. This is the PRR's hard blocker — **cannot be marked ready without a real provider key and a real scored run.** |
| 6 | Rollback rehearsed (deliberate bad deploy) | ✅ Ready, honestly scoped | S11 #120 — [`infra/deploy/rollout.sh`](../../infra/deploy/rollout.sh) real health-gate-then-rollback logic, rehearsed in CI ([`deploy.yml`](../../.github/workflows/deploy.yml)'s `rollback-rehearsal` job): a real API image is deployed with a deliberately broken config (unreachable DB), the script detects the failed readiness check and auto-reverts to the last-known-good image, and the reverted instance is confirmed to genuinely serve real requests again. **Honest scope note:** ADR-10.5 says "in staging" — this architecture's staging *is* the restore-drill's ephemeral output (ADR-10.3), and no real VPS/SSH target exists in this environment, so the rehearsal runs against a local/CI-ephemeral Docker host instead of a real remote one. The health-gate-and-rollback *decision logic* under test is identical either way; only the Docker host differs — see `rollout.sh`'s own header comment. |
| 7 | Cost caps enforced + alerting verified (synthetic burn test) | ✅ Ready | `GlobalBudgetNearCap` alert has a real `promtool` synthetic test crossing the 90% threshold and asserting the alert fires ([`infra/prometheus/tests/aether_test.yml`](../../infra/prometheus/tests/aether_test.yml)), run every CI `lint` job. Budget enforcement itself (usage ledger, hard cap) shipped in S4 with its own test suite. Eval-spend inclusion under the same cap (Ch.10 F-4) confirmed by design (dedicated metered eval workspace). |
| 8 | Docs 15-minute path verified | ✅ Ready | `make bootstrap` — README.md's own stated bound ("≤ 15 min, CI-verified monthly"), verified by [`bootstrap-timing.yml`](../../.github/workflows/bootstrap-timing.yml) (weekly cron + `workflow_dispatch`, real cold-machine timing, timeout enforces the SLA). Separately, the Devon-persona quickstart (S10 #110, [`docs/api/quickstart.md`](../api/quickstart.md)) proves a real end-to-end grounded-chat path in ~6.5s measured, far under any 15-minute bound. |
| 9 | Known-gaps register published | ✅ Ready (this document) | See below. |

## Known gaps register

Not a confession — per ADR-10.5, this is a PRR *output*: knowing what
shipped without something is the discipline. Every gap below has a
named trigger for revisiting it.

| Gap | Trigger to revisit |
|---|---|
| **North Star eval unscored** (checklist #5) — no real LLM provider key in this environment | A real provider API key becomes available; run the full eval harness against a release candidate and record the score here |
| **8 of 11 runbooks' own procedures not yet drilled end-to-end** (checklist #2) — their underlying alert/mechanism is tested, the document's exact steps are not (3 now are: `disaster-recovery.md`, `secrets-rotation.md`, `api-down.md`) | Each remaining runbook, drilled for real once, same pattern as those three — `rls-violation-detected.md` is the one exception needing a different approach (see checklist #2's note) |
| **MFA deferred** (ADR-7.6) | Phase 3 / real user base with elevated-risk accounts |
| **Single failure domain** (one VPS, ADR-10.1) | Real users → managed Postgres (HA) + second app node first |
| **Best-effort on-call** (solo maintainer, no PagerDuty-grade rotation) | A second maintainer or paid on-call tooling becomes available |
| **No multi-region** | Real users outside a single region's acceptable latency/compliance envelope |
| **No real VPS/SSH deploy target in this environment** (checklist #6's honest scope note) | A real server is provisioned; swap `rollout.sh`'s local `docker run` calls for the same logic over SSH against real compose services — the decision logic itself does not change |
| **Real provider-key rotation "new key is used" verification undischarged** (S10 #109's runbook) | A real provider key exists to rotate |

## How to re-run this review

- Re-check each ✅ line's CI link is still green on `main`.
- Re-run `apps/api/scripts/verify_restore_drill.py` and `deploy.yml`'s
  `rollback-rehearsal` job if either mechanism changes.
- Update the gaps register as items close; do not delete closed rows —
  strike them through with the closing evidence, so the review's own
  history stays auditable.
