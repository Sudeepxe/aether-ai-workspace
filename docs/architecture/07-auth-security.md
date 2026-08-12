# Chapter 7: Authentication, Authorization & Security

> **Status vs. implementation:** design (no implementation yet — updated per sprint, Ch. 9 F-4)
> Split from the frozen `blueprint.md`; do not edit here.


### 7.0 Decision D7-1: SPA Session Model (resolves OQ-3.3 / OQ-4.3)

**Chosen: hybrid — refresh token in an `httpOnly; Secure; SameSite=Lax` cookie path-scoped to `/v1/auth/refresh`; short-lived access JWT held in memory only (never localStorage), sent as `Authorization: Bearer`.**

1. **Why.** This splits the two theft vectors: XSS cannot read the httpOnly refresh cookie; CSRF cannot exploit the bearer header (attackers can't set headers cross-origin). The residual risks are explicitly bounded: XSS can use the in-memory access token *while the tab lives* (15-min ceiling, and if you have XSS the game is largely lost anyway — hence CSP as the real control), and CSRF surface shrinks to exactly one endpoint (`/refresh`), defended by SameSite + Origin-header validation + the fact that a forged refresh returns tokens to the victim's browser context, not the attacker.
2. **Alternatives.** **localStorage bearer tokens** — rejected flatly: any XSS exfiltrates long-lived credentials; indefensible in review. **Pure server-side sessions (opaque cookie)** — simple and revocable, but every API request pays a session-store lookup, CSRF defense extends to *every* mutating route, and the external API persona (Devon) needs bearer-style credentials anyway — two parallel auth paths. **BFF (backend-for-frontend) with token isolation** — the *most* secure pattern (tokens never reach the browser); rejected at v1 as an additional server hop and deployment unit for a solo project; recorded as the Phase-3 enterprise evolution path.
3. **Trade-off accepted:** in-memory access token dies on refresh/tab-open → silent re-auth via the refresh cookie on boot (one round-trip), coordinated across tabs per Ch. 5 F-1 (BroadcastChannel + Web Locks single-refresher).

### 7.1 Token Design & Rotation — Decision D7-2

- **Access JWT (15 min):** claims `{sub, tenant_id, role, scopes, jti, iat, exp, kid}`; **algorithm pinned to EdDSA (Ed25519)** — an allowlist of exactly one; `alg:none`/RS-HS confusion attacks die at the parser. `kid` resolves only against a static in-process key set (no JWKS-URL fetching from tokens — kid-injection dies too). Keys rotate via overlapping validity windows.
- **Refresh token (7 d, opaque random 256-bit, stored hashed):** **rotation on every use** with **family tracking**: each refresh issues a successor and marks the predecessor used; reuse of a used token ⇒ family compromise assumed ⇒ entire family revoked, user re-authenticates, security event logged. **Grace window (binding from Ch. 5 F-1):** a used token replayed within 30 s from the same device fingerprint returns the *same* successor instead of tripping revocation — absorbing multi-tab races and network retries without weakening the reuse signal (outside-window or cross-device reuse still revokes).
- **Why not opaque access tokens + introspection:** a store lookup on every request re-couples the hot path to Redis/PG availability (the thing JWTs decouple); revocation latency is instead bounded by the 15-min TTL + jti denylist for the high-severity cases (admin "log out user now", FR-ID-7). **Why not PASETO:** genuinely better defaults, thin ecosystem/tooling; the same properties are achieved by pinning EdDSA and banning dynamic headers — noted as the road-not-taken in the ADR.

### 7.2 OAuth2 / OIDC (social + future SSO)

Authorization-code **with PKCE** (public client), `state` for CSRF, exact-match registered redirect URIs (open-redirect class dies at registration), `nonce` validated on ID tokens. **Account linking is the classic takeover trap:** an OAuth identity auto-links to an existing email account **only if the IdP asserts the email verified**; otherwise the flow requires login to the existing account first, then explicit linking. Unverified-email auto-link is the vulnerability that has hit real products (see mistakes list). Phase 3 SSO (FR-ID-5): standard OIDC against Okta/Entra with JIT provisioning — the v1 identity model (external `identities` table separate from `users`, Ch. 8) is shaped for it now so SSO is additive.

### 7.3 Authorization — RBAC Matrix & Enforcement Stack

| Capability | Viewer | Member | Admin | Owner |
|---|---|---|---|---|
| Read threads/docs/search | ✓ | ✓ | ✓ | ✓ |
| Create threads/messages | — | ✓ | ✓ | ✓ |
| Upload/delete documents | — | ✓ | ✓ | ✓ |
| Invite/remove members, set roles | — | — | ✓ | ✓ |
| Budgets, model policy, API keys | — | — | ✓ | ✓ |
| Audit log read | — | — | ✓ | ✓ |
| Workspace delete/export/transfer | — | — | — | ✓ |

Enforcement is the **already-established stack, referenced not re-invented:** central policy map (single source; the matrix above *is* the artifact) → deny-by-default route declarations (ADR-4.5) → repository tenant-typing → RLS (§3.7.2). Invariants: ≥ 1 Owner always (last-owner protection, §4.3); role changes take effect ≤ access-token TTL, immediately for revocation-grade actions via jti denylist; **authz-matrix tests are generated from the policy map** — every (route × role) combination asserted in CI, so the matrix cannot drift from the code (test count ≈ 40 routes × 4 roles, cheap, exhaustive).

### 7.4 API Keys, Secrets, Cryptography

- **API keys:** format `aeth_{env}_{8-char-prefix}{32-byte-random}` — prefix stored plaintext for identification, secret stored SHA-256 (fast hash is correct here: 256-bit random keys are unbrute-forceable, unlike passwords); scoped to workspace + explicit scopes (`chat:write`, `kb:read`, …); expiry optional but nudged; last-used timestamp (coarse, hourly — avoids a write per request); revocation immediate (keys are looked up per request by design — the bearer-JWT trade-off analysis does not apply to machine credentials, and per-key rate limits need the lookup anyway).
- **Passwords:** argon2id (memory 64 MB, t=3 — OWASP-current parameters recorded as tunables), per-user salt, breach-list check at set time (offline top-100K list; no external calls with user passwords), constant-time verify, enumeration-safe flows (identical timing/response for unknown-user vs. wrong-password).
- **Secrets management:** runtime secrets injected via environment from the deploy layer — **SOPS + age** encrypts the config bundle in-repo (auditable, versioned, no plaintext at rest in git); cloud profile swaps to the platform secret manager with the same injection contract. Provider API keys additionally **envelope-encrypted at rest in PG** (data key wrapped by a master key held only in the secret manager) — DB backup theft alone yields no provider credentials. Rotation runbook per secret class; `gitleaks` in CI as the tripwire (NFR-SEC-2).
- **Transport/at-rest:** TLS 1.3 everywhere external; internal compose network isolated; PG + object storage encrypted at rest (disk/provider level); backups encrypted with a *separate* key (backup theft ≠ data theft).

### 7.5 Attack-Surface Review (auth-plane specifics beyond §3.7)

| Attack | Control |
|---|---|
| Credential stuffing / brute force | Per-IP + per-account limits with jittered lockout (no fixed threshold observable), argon2id cost, breach-list at set |
| Session fixation | Session identifiers issued only post-authentication; refresh rotation regenerates lineage on login |
| JWT `alg`/`kid` games | Single pinned algorithm; static key set; no token-driven key resolution |
| Refresh theft (cookie exfil via subdomain/XSS-adjacent) | Path-scoped cookie, `__Host-` prefix (no subdomain leakage), rotation + family reuse detection bounds stolen-token lifetime to one use |
| Invitation abuse | 128-bit single-use tokens, 7-day expiry, inviter-visible audit, `auth`-class rate limit |
| OAuth account takeover | Verified-email gate + explicit linking (§7.2) |
| Enumeration (login/registration/invite) | Uniform responses + timing normalization |
| Admin-plane compromise | Owner-only destructive ops, audit on every admin action, session revocation lever (FR-ID-7); MFA/TOTP is Phase 3 — *documented as the known v1 gap in the security posture, not silently omitted* |

### 7.6 Security Testing, Monitoring, Failure Modes, Cost

- **Testing (feeds Ch. 10):** generated authz-matrix suite (above); cross-tenant red-team suite (§3.7.2 layer 8); token-lifecycle tests (rotation, reuse-revocation, grace window, expiry skew); OAuth flow tests against a mock IdP; `gitleaks` + `pip-audit` + ZAP baseline in CI; secrets-in-image scan (trivy).
- **Monitoring:** login failure spikes (per-IP/per-account), refresh-reuse events (page — each one is either an attack or a client bug, both matter), revocation-list size, API-key anomaly (volume/new-IP per §3.7.3), OAuth callback error rates.
- **Failure modes:** IdP down → password auth unaffected (§3.2.2); Redis down → ADR-3.6 fail-open posture with 15-min bound; signing-key compromise → rotate via kid overlap + global refresh-family revocation (runbook'd); *auth DB down → the system is down, correctly* — no cached-credential fallback (an explicit non-mitigation: availability workarounds for the identity store are how horror stories start).
- **Cost:** zero marginal infra; argon2id CPU (~50 ms/login) is the deliberate price of password security.

### 7.7 ADRs, Interview Q&A, Mistakes, Roadmap, Checklist

| ADR | Decision | Revisit trigger |
|---|---|---|
| ADR-7.1 | Hybrid session: httpOnly path-scoped `__Host-` refresh cookie + in-memory bearer access (D7-1); BFF = Phase-3 enterprise path | Enterprise deployment demands BFF |
| ADR-7.2 | EdDSA-pinned 15-min JWT + rotating hashed refresh with family reuse-detection and 30-s same-device grace (D7-2) | — |
| ADR-7.3 | OAuth verified-email gate + explicit linking; PKCE mandatory | — |
| ADR-7.4 | Policy-map-generated authz tests (matrix cannot drift from code) | — |
| ADR-7.5 | SOPS+age secrets in-repo; envelope encryption for stored provider keys | Cloud secret manager in cloud profile |
| ADR-7.6 | MFA deferred to Phase 3 — recorded as a known, stated gap | Enterprise tier or real users |

**Interview Q&A.** *Q1: "Where do you store tokens in the browser and why?"* — Ideal: rejects localStorage unprompted (XSS exfil), explains the split-vector hybrid (httpOnly refresh vs. in-memory bearer), names the residual XSS-while-tab-lives risk and why CSP is the actual control, and the multi-tab refresh coordination. *Q2: "Walk me through refresh-token reuse detection and its false positives."* — Ideal: rotation + family revocation as theft detection; then the mature part — legitimate races (multi-tab, retry) trip it, hence same-device grace window; reuse outside the window is a page-level event. *Q3: "Why SHA-256 for API keys but argon2id for passwords?"* — Ideal: threat model, not cargo cult — passwords are low-entropy (need memory-hard slowness); 256-bit random keys are unguessable (fast hash fine, and per-request verification must be cheap). *Q4: "How does a role change propagate to someone currently logged in?"* — Ideal: bounded by access-token TTL for downgrades; revocation-grade actions use the jti denylist for immediacy; names the trade (per-request Redis check) and why it's paid only for the denylist path. *Q5: "Your OAuth flow — where's the account-takeover bug usually hiding?"* — Ideal: unverified-email auto-linking; attacker registers at IdP with victim's email unverified → auto-link = takeover; gate on IdP-verified email + explicit linking otherwise.

**Common mistakes.** localStorage JWTs; long-lived access tokens "for convenience"; rolling your own password hashing or comparing digests non-constant-time; JWKS/`kid` resolved from attacker-controlled input; refresh tokens that never rotate (theft = permanent access); OAuth auto-link without email verification; authz checks scattered per-handler and drifting from the documented matrix; secrets in `.env` committed "temporarily"; lockout thresholds that enable victim-lockout DoS (hence jitter + per-IP-and-account separation); logging bearer tokens in access logs.

**Roadmap.** Phase 3: MFA/TOTP + WebAuthn, OIDC SSO + SCIM, BFF option for enterprise, per-tenant encryption keys (BYOK), anomaly-based session scoring. 3-year: the identity module is the second-most-likely extraction candidate (after the router) if a second product appears — its port boundary (Ch. 3 hexagon) is already service-shaped.

**Checklist:** no plaintext secrets anywhere in repo/images (CI-verified) ✓; every auth flow enumeration-safe ✓; token theft bounded in time on every axis (15-min access / one-use refresh / revocable keys) ✓; authz matrix machine-enforced ✓; known gaps (MFA) stated, not hidden ✓; Ch. 5 F-1 binding satisfied (grace window) ✓.

### 7.8 Self-Review Record — Chapter 7

| Finding | Severity | Resolution |
|---|---|---|
| F-1: Draft cookie spec lacked `__Host-` prefix — a subdomain (or subdomain takeover) could shadow the refresh cookie | Medium | `__Host-` prefix mandated (locks Secure, no Domain attribute, path from `/`) with path scoping to the refresh route (§7.4 table, ADR-7.1) |
| F-2: Ch. 5 F-1's grace-window requirement risked *weakening* reuse detection if scoped too loosely (any reuse within 30 s forgiven ⇒ attacker races victim) | **High** | Grace window narrowed: same device fingerprint + returns the *same* successor (idempotent refresh) rather than issuing fresh lineage — an attacker replaying from elsewhere still trips family revocation (§7.1) |
| F-3: Audit events initially specified storing actor email — PII in an immutable, long-retention store conflicts with erasure obligations (§1.5) | Medium | Audit stores `user_id` (pseudonymous, join-resolved at read time); user hard-delete tombstones the join, preserving audit integrity without retaining PII — schema obligation passed to Ch. 8 |
| F-4: "Last-used" on API keys as a per-request write = write amplification on the hottest path | Low | Coarsened to hourly upsert via buffered async update (§7.4) |

**Verdict:** pass. F-2 is the instructive one: a fix imported from another chapter (Ch. 5's multi-tab race) nearly introduced a *worse* vulnerability than the bug it fixed — cross-chapter obligations need their own security review on arrival, which is exactly what this record demonstrates. F-3 hands a binding constraint to Chapter 8 (audit PII-minimization), keeping the obligation chain unbroken.

---

