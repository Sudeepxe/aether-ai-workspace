# ADR-10.4: Symptom-based paging with named runbooks; blameless postmortems published in-repo

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Alerting design needed a philosophy decision — page on underlying causes such as high CPU, versus page on user-visible symptoms such as SLO burn rate — the former is a well-known source of alert fatigue and ignored pages.

## Decision

Page only on SLO-symptom-level conditions, such as availability burn rate, time-to-first-token burn rate, sustained dead-letter-queue depth, outbox lag, security-relevant events like refresh-token reuse, row-level-security violations, certificate expiry, disk usage, and budget thresholds, with every page-grade alert naming its verifying runbook. Lower-severity conditions become tickets, not pages. Incidents get a blameless postmortem, including a timeline, contributing factors, and action items with owners and dates, published in-repo, including for self-inflicted incidents.

## Alternatives considered

- **Cause-based alerting, paging on raw resource metrics like CPU or memory directly** — rejected — the classic alert-fatigue pattern where causes get their own dashboards but don't independently justify waking someone up; causes are demoted to dashboard-only signals.

## Consequences

Easier: pages are meaningful because they map directly to user-visible SLO impact, with a known remediation path attached to each one. Harder: a solo on-call reality means paging is realistically a push notification plus email, not a 24/7 team rotation — explicitly framed as best-effort ops with production-shaped discipline, not enterprise-grade on-call.

## Revisit trigger

None stated.
