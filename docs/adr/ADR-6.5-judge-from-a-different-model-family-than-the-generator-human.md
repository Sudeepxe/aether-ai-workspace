# ADR-6.5: Judge from a different model family than the generator, human-calibrated, tiered eval spend, fail-closed merges on AI paths

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The North Star metrics (faithfulness, correct refusal) need an automated, CI-runnable measurement mechanism that is trustworthy and cost-bounded, since human-only evaluation doesn't scale to CI and an LLM judging itself risks self-preference bias.

## Decision

Use an LLM judge from a different model family than the generator, calibrated against a roughly 30-case human-labeled slice with agreement tracked over time. Execution is cost-tiered: a 20-case path-filtered smoke test on PRs, a 150-case full run nightly, and a full run plus performance overlay on release. A provider outage during evaluation causes merges to AI-affecting paths to block rather than proceed unverified.

## Alternatives considered

- **RAGAS/DeepEval as the harness** — rejected for core for the same reasoning as ADR-6.1 — the harness is itself a demonstration artifact — though standard metric definitions are borrowed where appropriate.

- **Human-only evaluation** — doesn't scale to CI.

- **Judge-only without calibration** — produces un-anchored numbers that drift silently as the judge model itself is updated over time.

## Consequences

Easier: faithfulness and refusal become measured, trended numbers rather than vibes, with cost staying bounded via the tiered execution strategy. Harder: judge-model updates could still drift scores even with calibration — mitigated by tracking human-agreement over time as a canary, not a one-time check.

## Revisit trigger

Judge agreement drops below 80% versus the human-labeled slice.
