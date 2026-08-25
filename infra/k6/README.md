# k6 performance budgets (S9 #98, ADR-2.4, §10.5)

Three scripts, each budgeting one blueprint NFR against a real running
API + worker (not mocks):

| Script | Budgets | Threshold |
|---|---|---|
| `non-ai-crud.js` | NFR-P-3 (non-AI endpoints) | `GET /v1/me` p95 < 200ms |
| `ungrounded-chat.js` | NFR-P-1 (ungrounded) | chat-turn p95 < 800ms |
| `grounded-chat.js` | NFR-P-1 (grounded) | chat-turn TTFT p95 < 1500ms |

## Honest gaps (documented, not faked)

- **NFR-P-2** ("retrieval sub-step p95 < 400ms") has no standalone k6
  script: there's no dedicated retrieval-only HTTP endpoint to isolate
  it from generation — retrieval timing is bundled inside
  `grounded-chat.js`'s end-to-end TTFT. Isolating it would need either
  a new test-only endpoint or server-side retrieval-duration
  instrumentation, neither of which exists yet. Tracked as a real
  follow-up.
- **"Ungrounded" no longer has a distinct generation code path.** Since
  S6, every chat turn runs real hybrid retrieval before generation — an
  empty-KB workspace always clears zero chunks, so Gate 1 (ADR-6.4)
  refuses before the generator is ever called. `ungrounded-chat.js`
  measures that refusal fast-path instead, the closest real analogue
  left — see the script's own header comment.
- **"Pinned hardware"** (§10.7's nightly-absolute-budget premise)
  doesn't exist in this environment — CI runs on a shared GitHub
  Actions runner, not a dedicated perf box. A CI regression is still a
  real signal; the absolute numbers just carry more noise than a real
  pinned runner would. Same class of deferral as the S11 dedicated VPS.
- **PR-time "relative regression"** (ADR-2.4's stated design: compare
  against a stored main-branch baseline) is not implemented — these
  thresholds are absolute (the literal NFR-P-1/2/3 values), enforced
  the same way on every PR and nightly. A real relative-regression
  comparison needs a baseline-artifact storage mechanism (e.g. a k6
  summary JSON published from every main-branch merge, diffed against
  on each PR) that doesn't exist yet. Tracked as a real, scoped
  follow-up — not faked with a comparison against nothing.

## Running locally

Needs a real running API + worker (`make dev` infra, then the API and
worker started directly — see `.github/workflows/ci.yml`'s `e2e` job
for the exact boot sequence this mirrors):

```
docker run --rm -i --network host \
  -e AETHER_BASE_URL=http://localhost:8000 \
  grafana/k6:0.55.0 run - < infra/k6/non-ai-crud.js
```

Pre-release soak (§10.5: "1h at envelope concurrency"):

```
docker run --rm -i --network host \
  -e AETHER_BASE_URL=http://localhost:8000 -e K6_VUS=20 -e K6_DURATION=1h \
  grafana/k6:0.55.0 run - < infra/k6/grounded-chat.js
```
