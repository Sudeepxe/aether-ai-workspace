# Deliberate trade-offs

Phase 8 (`docs/REMEDIATION_PLAN.md`) asks that things missing on purpose
be written down as trade-offs, not left to be mistaken for oversights.

## No authenticated provider smoke test in CI

**What's absent:** no CI job makes a real, authenticated call to Groq,
OpenAI, or Anthropic on every PR (or on any schedule) to catch live
integration drift (an API shape change, a deprecated model, a pricing
change) before it reaches a real user.

**What exists instead:** `.github/workflows/eval-nightly.yml` is wired
to accept `AETHER_OPENAI_API_KEY`/`AETHER_ANTHROPIC_API_KEY` as repo
secrets *if they are ever configured* — they are not, today. Every
AI-facing component falls back to an honest, clearly-labeled local
placeholder (`EchoGenerator`, `LocalHashEmbeddingAdapter`) in dev and
CI, and the eval harness's own judge reports `not_measured` rather than
fabricating a score when no real provider is available. All of this
session's real-provider validation (Phase 7's adversarial and
faithfulness runs, this phase's exfiltration-mitigation re-test) was
run locally, once, on demand, against the real Groq API — never in CI.

**Why this is deliberate, not an oversight:**

- **Secret management.** A CI-resident, always-available provider API
  key is a materially larger exposure surface than a SOPS/age-encrypted
  key decrypted only in a developer's own shell for the duration of one
  manual command (this engagement's own working pattern throughout
  Phase 7 and this phase: `eval "$(./infra/secrets/env-export.sh
  infra/secrets/dev.enc.yaml)"`, scoped to one shell invocation). Every
  CI run, on every PR, from every contributor, would be a new
  opportunity for that key to leak into a log, an artifact, or a forked
  PR's workflow context.
- **Flakiness.** This session's own real runs hit both Groq's per-minute
  rate limit (handled by real exponential backoff, `evals/harness/live_provider.py`)
  and its harder daily quota (200,000 tokens/day on the free tier — no
  useful backoff exists for that; retry-after values ran 10-40+ minutes)
  partway through a single afternoon of manual testing. A CI job with
  the same free-tier key, run automatically on every PR, would fail
  intermittently for reasons that have nothing to do with the PR's own
  changes — the exact failure mode CI is supposed to avoid.
- **Cost.** Free-tier quota is the only tier configured in this
  environment. A paid tier removes the daily-quota flakiness above but
  turns "run on every PR" into a real, recurring cost tied to
  contribution volume rather than to anything the codebase does.
- **Non-determinism as a CI gate.** Phase 7 itself found the target
  model materially non-deterministic on identical input (the
  system-prompt-exfiltration fixture: 3 of 4 real trials leaked, 1
  didn't — same code, same fixture, same config). A CI gate that must
  produce a stable pass/fail on every PR is a poor fit for a system
  whose real behavior varies run to run; either the gate is flaky by
  the model's own nature, or it has to be so lenient it stops catching
  real regressions.

**What would change this decision:** a paid provider tier removes the
daily-quota flakiness (though not the model non-determinism); a
short-lived, per-run-scoped credential (rather than a long-lived repo
secret) would meaningfully reduce the secret-management exposure. Both
are infrastructure/budget decisions outside this engagement's scope,
not technical blockers — if either changes, the honest next step is a
narrowly-scoped smoke test (one or two fixed, low-token-cost fixtures,
not the full adversarial/faithfulness suites) on a schedule sparse
enough to respect the quota, not a full-suite gate on every PR.

## Other places something is missing on purpose

- **No local semantic embedder.** `LocalHashEmbeddingAdapter` (hash
  expansion of a text's own bytes) is the only embedder configured in
  this environment — deliberate, not accidental: adding a local
  sentence-transformers adapter means a `vector(1536)`-fixed column
  needs to accommodate a different real dimensionality
  (all-MiniLM-L6-v2 is 384), and ADR-8.4 explicitly gates that migration
  on the recall-verification work Phase 6 exists to do. Every retrieval
  number in `PHASE5_RESULTS.md`/`PHASE6_RESULTS.md` is a real,
  measured number under this constraint — stated as a lower bound on
  what a real semantic embedder would achieve, not a ceiling.
- **No cross-family faithfulness measurement.** ADR-6.5 requires a
  judge from a different model family than the generator. This
  environment has only Groq configured, so `judge_faithfulness()`
  correctly and honestly returns `not_measured` for every real reply in
  Phase 7's RUN 2 (35/35) — this is the design working as intended for
  a single-provider environment, not a bug, and not evidence about
  faithfulness one way or the other.
