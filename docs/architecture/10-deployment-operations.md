# Chapter 10: Deployment, DevOps, CI/CD, Monitoring, Testing & Production Readiness

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 10.0 Decision D10-1: Demo Production Environment — Single VPS + Compose

**Chosen: one 4 vCPU / 8 GB VPS (Hetzner/DigitalOcean-class, ~$24/mo)** running the `demo` compose profile (api ×2, worker, Caddy with HTTP/2 + auto-TLS — discharging Ch. 4 F-1, PG, Redis, MinIO, LGTM stack), plus offsite object storage (Cloudflare R2) for backups.

- **Alternatives:** **PaaS (Fly.io/Render/Railway)** — genuinely attractive (managed TLS, deploy UX); rejected because the multi-container topology + LGTM stack fragments across their pricing/units, and the *operational story is the portfolio* — "I run it on a box with runbooks and drills" demonstrates ops skill a PaaS abstracts away. **Managed K8s (EKS/GKE)** — already rejected as operational theater (ADR-3.9); the readiness checklist is verified instead. **Serverless** — rejected in D3-1 (SSE, local-first). Honest company-context note: for a real startup, PaaS or managed containers is the right first answer; this choice optimizes for *demonstrated ops competence*, which is a different objective.
- **Failure domain honesty (restated from §3.14):** one box = all eggs; accepted at demo tier with RPO/RTO per §3.9.4, and *stated in the public docs* — pretending otherwise would fail review.

### 10.1 Environments & Promotion Path

| Env | Where | Purpose | Data |
|---|---|---|---|
| `dev` | Laptop compose (`dev` profile: hot reload, Ollama, mailpit) | Daily work; zero cloud deps (NFR-PT-1) | Synthetic seed |
| `test` | Ephemeral, in CI (`test` profile via testcontainers/compose) | Integration/e2e/perf per run; born clean, dies clean | Fixtures |
| `staging` | **Ephemeral on the VPS** — the demo stack redeployed to a parallel compose project on release candidates only | Pre-prod smoke + migration rehearsal against a restored prod snapshot | Restored (synthetic) snapshot |
| `prod` (demo) | VPS `demo` profile | The public artifact | Real demo tenants |

Persistent staging rejected deliberately: it doubles cost to protect a demo, and *ephemeral staging built from a restored backup doubles as the DR drill* — one mechanism, two duties (the restore-drill workflow *is* the staging provisioner).

### 10.2 CI/CD Pipeline (GitHub Actions, path-filtered, all jobs local-replicable via `make`)

**PR pipeline (< 10 min target):** ruff+mypy+eslint+import-boundary lint → unit tests (domain against fake ports — seconds, no I/O) → contract tests (OpenAPI diff + schemathesis against the app in-process) → integration (real PG+Redis via testcontainers: RLS suite, outbox, migrations against prod-shaped snapshot + N−1 compat) → **security gates:** gitleaks, pip-audit/npm-audit, trivy (fail: critical CVEs with documented allowlist process) → **eval smoke** (20 cases, only on AI-path changes — §6.4) → frontend unit + bundle-size budget → e2e (Playwright vs. `test` profile compose: golden paths incl. refusal + cross-tenant denial) → **cross-tenant red-team suite** (§3.7.2 L8 — a failure here blocks everything, no override).

**Merge-to-main adds:** image builds (multi-stage, SBOM via syft, cosign-signed, pushed to GHCR by digest) → k6 perf smoke (relative-regression thresholds per ADR-2.4) → auto-deploy (below).

**Nightly:** full eval (150 cases, trend-charted → `docs/evals/`) · k6 absolute budgets on pinned hardware · dependency scans · bare-runner bootstrap timing (Ch. 9 F-1). **Quarterly (scheduled):** restore drill = staging provision + e2e green against restored snapshot + HNSW rebuild timing vs. RTO + RLS assertion (§8.5).

### 10.3 Deployment Strategy — Decision D10-2

**Chosen: push-based deploy from CI over SSH — pull new digests, `docker compose up -d` with rolling replace (api ×2 replaced one at a time behind Caddy health checks; SSE drain ≤ 60 s per NFR-A-2), migrations run *before* rollout (expand-contract makes N−1 safe, ADR-8.5), post-deploy smoke (healthz, login, one grounded turn against a demo tenant, one refusal) → failure auto-rolls back to previous digests (kept warm).**

- **Alternatives:** **Watchtower/poll-based** — rejected: deploys should be *events with authors, logs, and gates*, not eventual conditions. **GitOps (Argo/Flux)** — the right answer on K8s; without K8s it's machinery without a substrate; revisit with ADR-3.9. **Blue-green on one box** — 2× memory footprint exceeds the VPS for marginal gain over health-gated rolling; rejected. Canary releases — meaningless at demo traffic volume; the post-deploy smoke *is* the canary.
- **Deploy cadence target:** trunk-based, deploy on every green merge — DORA-elite behaviors at portfolio scale (frequency + lead time measurable from history, §9.5).

### 10.4 Monitoring, Alerting & Incident Response

- **Dashboards (Grafana, provisioned as code in `infra/`):** ① SLO overview (availability, TTFT p95, error-budget burn) ② AI plane (§6.7: faithfulness trend, refusal/hallucinated-citation rates, cost per turn, provider TTFT/fallbacks) ③ Ingestion (queue depth, per-stage timing, DLQ, per-tenant fairness) ④ Data tier (pool saturation, replication-ready metrics, HNSW memory, outbox lag) ⑤ Cost (spend vs. caps, estimate-vs-actual drift).
- **Alert policy (symptom-based, §3.8; every alert names its runbook):** *Page-grade:* SLO burn-rate multiwindow (2%/1 h ∧ 5%/6 h), API down, DLQ > 0 sustained 15 min, outbox lag > 5 min, refresh-reuse security event (§7.6), RLS violation (any), cert expiry < 14 d, disk > 80%, global budget ≥ 90%. *Ticket-grade:* eval-score regression (nightly), flaky-test quarantine, dependency criticals, estimate-drift > 5%. Solo on-call reality: page = push notification + email; the honest framing is "best-effort ops with production-shaped discipline," and MTTR is measured anyway.
- **Incident response:** runbook per page-alert (Ch. 9's testable-docs rule); blameless postmortem template (timeline, contributing factors, action items with owners/dates) — *used for real incidents including self-inflicted ones*; postmortems published in-repo (an incident honestly documented is portfolio gold, not embarrassment).

### 10.5 Testing Strategy (the full pyramid, consolidated)

| Layer | Tooling | Scope & gate |
|---|---|---|
| Unit (widest) | pytest / Vitest | Domain logic vs. fake ports: chunking, token budgeting, prompt assembly, RRF/MMR, policy map, SSE parser, stream state machine. Coverage ≥ 80% on core (NFR-M-1); mutation-testing (mutmut) sampled quarterly on domain — coverage honesty check |
| Contract | schemathesis + OpenAPI diff | Every route conforms to its published schema; spec drift fails (ADR-9.2) |
| Integration | testcontainers (PG+Redis) | Repositories under **forced RLS**, outbox dispatch, migrations incl. N−1, deletion cascade evidence (test named `test_FR_KB_5_deletion_cascades`, §2.9) |
| Security | Generated authz matrix (§7.3) · cross-tenant red team (L8) · ZAP baseline · gitleaks/trivy | Any failure blocks merge, no override path |
| E2E | Playwright vs. compose | Golden paths: signup→invite→upload→grounded-cited answer→**refusal**→deletion; mocked-SSE determinism + one live smoke (§5.5) |
| Eval (the AI layer) | Owned harness (§6.4) | Smoke on PR (path-filtered) / full nightly / release; North Star gates release |
| Performance | k6 | PR: relative regression; nightly: absolute budgets (NFR-P-1/2/3) on pinned runner; soak: 1 h at envelope concurrency before each release tag |
| Chaos-lite | Scripted in staging window | Kill Redis (assert §3.2.12 degraded modes), kill a provider stub mid-stream (assert SD-1 fallback + partial handling), kill worker mid-ingest (assert resume) — each asserts a *specific documented degraded behavior*, not vibes |

**Test-data policy:** factories + synthetic corpora only (ADR-9.5); prod data never leaves prod (staging restores are synthetic-tenant snapshots).

### 10.6 Production Readiness Review (Google-PRR-style gate for v1.0)

Launch blocks unless all green: SLOs defined+dashboarded+alerted ✓ · runbooks exist and each was executed once for real ✓ · restore drill passed within RPO/RTO ✓ · security gates green incl. red-team suite ✓ · North Star eval ≥ 90/90 on release candidate ✓ · rollback rehearsed (deliberate bad deploy in staging) ✓ · cost caps enforced + alerting verified (synthetic burn test) ✓ · docs 15-min path verified ✓ · known-gaps register published (MFA Phase 3, single-box failure domain, best-effort on-call) ✓ — *the gap register is a PRR output, not a confession: knowing what you shipped without is the discipline.*

### 10.7 Cost Ledger (demo tier, monthly)

VPS $24 · R2 backups/objects ~$3 · domain ~$1 · GHCR/Actions $0 (free tier + path filtering) · LLM cap $50 hard (≈ $15–25 typical incl. nightly evals ~$5) → **worst case ≈ $80/mo, typical ≈ $55/mo.** Scale-out price points documented: +$10 worker node → 2× ingestion; +$19 managed PG (HA) when the single-box posture retires; the first *architectural* spend is Qdrant extraction (D3-2) at ~$30/mo — every trigger has a price tag attached in advance.

### 10.8 ADRs, Interview Q&A, Mistakes, Roadmap, Checklist

| ADR | Decision | Revisit trigger |
|---|---|---|
| ADR-10.1 | Single VPS + compose for demo prod; ops-competence over PaaS convenience (D10-1) | Real users → managed PG + second node first |
| ADR-10.2 | CI-push SSH deploy, health-gated rolling, auto-rollback on failed smoke (D10-2) | K8s adoption → GitOps |
| ADR-10.3 | Ephemeral staging = restore-drill output (one mechanism, two duties) | Persistent staging when real users exist |
| ADR-10.4 | Symptom-based paging with named runbooks; blameless postmortems published in-repo | — |
| ADR-10.5 | Release gate = PRR checklist incl. eval North Star + rehearsed rollback | — |

**Interview Q&A.** *Q1: "Walk me through what happens between `git push` and production."* — Ideal: narrates the full pipeline with *why* per stage (path filtering for feedback speed, digest-pinned signed images for supply chain, migrations-before-rollout because expand-contract, smoke-as-canary, auto-rollback) and names the trade of trunk-based solo deploys (no human gate — compensated by gates that don't sleep). *Q2: "How would you know your monitoring works?"* — Ideal: alerts are tested like code — synthetic burn test for budget alerts, chaos-lite kills asserting degraded-mode alarms fire, quarterly drill exercising the pager path; monitoring that's never fired is unverified code. *Q3: "Your only box dies at 2 am. Timeline?"* — Ideal: RPO ≤ 1 h (WAL to R2), RTO ≤ 4 h: new VPS from infra scripts → restore drill procedure (rehearsed quarterly, timed) → vectors rebuild within budget (ADR-2.3 arithmetic) → DNS cutover; names what's lost (in-flight generations, ≤ 1 h of writes) and where that's documented to users. *Q4: "Why is your staging ephemeral, and what does it cost you?"* — Ideal: names the reuse (staging = tested backup restore), the savings, and the honest cost — no long-lived soak environment, mitigated by the pre-release soak run; shows cost-consciousness as an engineering dimension, not stinginess. *Q5: "What's deliberately NOT production-grade here?"* — Ideal: recites the gap register unprompted — single failure domain, best-effort on-call, MFA deferred, no multi-region — each with its trigger-priced upgrade path; knowing the boundary of your system's claims is the staff-level tell.

**Common mistakes.** Testing the happy path of deploys but never the rollback; migrations coupled to deploy scripts (can't roll back code independently); alerts on causes (CPU!) not symptoms (SLO burn) → pager fatigue → ignored pages; staging that drifts from prod until it tests nothing; backups never restored ("Schrödinger's backup"); coverage worship (80% of getters, 0% of the deletion saga); load tests against empty databases; chaos experiments without a hypothesis; secrets in CI logs via echo'd env; treating the PRR as paperwork after the launch date is fixed.

**Roadmap.** Phase 2: preview environments per PR (compose project per branch, TTL'd); trace-based alerting (exemplars). Phase 3: managed PG + second app node (retire single-box), OpenTelemetry SaaS export option for enterprise deployments, SLSA provenance level upgrade. 100×: the K8s migration ADR-3.9 anticipated — manifests derive from the compose topology + readiness checklist already verified.

**Checklist:** every NFR from §2.2 now has its verifying mechanism live in this chapter's pipeline (the §2.2 traceability rule closes here — audited in §11) ✓; every alert → runbook → automation chain complete ✓; rollback rehearsed not assumed ✓; eval gates wired into release ✓; cost caps burn-tested ✓; gap register published ✓.

### 10.9 Self-Review Record — Chapter 10

| Finding | Severity | Resolution |
|---|---|---|
| F-1: Draft deploy pulled images by tag (`:latest`-style) — a re-pushed tag could silently change what "rollback" restores | **High** | All pulls/rollbacks by **digest**; digests recorded per deploy in an in-repo deploy log; cosign verification at pull — rollback is now bit-identical by construction |
| F-2: Secrets decrypted at deploy landed in a compose `.env` on the VPS disk in plaintext, undermining ADR-7.5's at-rest posture | Medium | Deploy renders secrets to a root-owned tmpfs mount consumed via compose secrets; nothing persists on disk; VPS disk-encryption noted as baseline |
| F-3: Migrations-before-rollout with api ×2 rolling means old code briefly writes against new schema — stated, but no gate *proved* N−1 compatibility per release | Medium | The integration suite's N−1 job (Ch. 8) promoted to a **required release check** against the actual RC pair (previous digest + new schema), closing the assertion-vs-proof gap |
| F-4: Budget-alert verification was designed but eval-spend itself was outside the metering path (nightly evals bill the same provider keys) | Low | Eval harness uses a dedicated metered workspace under the same budget machinery — the $50 cap governs *all* spend incl. CI/evals; drift alarm covers it |
| F-5: Chaos-lite lacked pass criteria in draft ("kill Redis and observe") | Low | Each experiment now asserts a named documented behavior (degraded-mode table §3.2.12, SD-1 fallback) — hypothesis-driven per best practice |

**Verdict:** pass. F-1 is the classic — mutable tags make rollback a lie under exactly the conditions (compromised or confused registry state) where rollback matters most. F-3 upgraded a compatibility *claim* into a per-release *proof*, consistent with the blueprint's recurring theme: assertions must carry falsifiers.

---

