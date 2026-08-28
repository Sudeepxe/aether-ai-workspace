// Shared setup helpers for the k6 performance-budget scripts (S9 #98,
// ADR-2.4). Every script needs "a real authenticated user in a real
// workspace" — factored out once rather than duplicated three times.
import http from "k6/http";
import { check, sleep } from "k6";

export const BASE_URL = __ENV.AETHER_BASE_URL || "http://localhost:8000";

// The AUTH rate-limit class (10 req/60s, §7.5) is IP-scoped — there's
// no user identity yet at register time — so every VU launching at
// once and registering simultaneously is a real thundering herd
// against one shared bucket (the same root cause issue #83's e2e
// flakiness investigation found for Playwright's parallel workers;
// caught here empirically too, not assumed). Call once per VU, before
// its first registerAndLogin.
export function staggerVuStart() {
  sleep(__VU * 2);
}

// A single stagger delay isn't sufficient on its own — in real CI, one
// script's AUTH-bucket consumption (register+login for every VU) may
// not have fully refilled (continuous refill, 1 token/6s) by the time
// the *next* script in the same job starts registering its own users
// against the same IP-scoped bucket. Found empirically on the first
// real CI run (a local run only ever exercised one script's registration
// burst in isolation, never back-to-back scripts sharing one bucket).
// The robust fix is retrying on a genuine 429 with real backoff — how
// any real client should treat a rate-limited endpoint — not trying to
// time everything perfectly in advance.
function postWithRetry(url, body, params, checkLabel) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = http.post(url, body, params);
    if (res.status !== 429) {
      check(res, { [checkLabel]: (r) => r.status === 200 || r.status === 201 });
      return res;
    }
    const retryAfter = Number(res.headers["Retry-After"]) || 6;
    sleep(retryAfter);
  }
  throw new Error(`${checkLabel}: exhausted retries, still 429 after 5 attempts`);
}

export function registerAndLogin(emailPrefix) {
  const email = `${emailPrefix}-${__VU}-${Date.now()}@example.com`;
  const password = "s3cret!!";
  const jsonHeaders = { headers: { "Content-Type": "application/json" } };
  postWithRetry(
    `${BASE_URL}/v1/auth/register`,
    JSON.stringify({ email: email, password: password, display_name: email }),
    jsonHeaders,
    "register succeeded"
  );
  const loginRes = postWithRetry(
    `${BASE_URL}/v1/auth/login`,
    JSON.stringify({ email: email, password: password }),
    jsonHeaders,
    "login succeeded"
  );
  return loginRes.json("access_token");
}

export function createWorkspaceAndThread(token, name) {
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const wsRes = http.post(
    `${BASE_URL}/v1/workspaces`,
    JSON.stringify({ name: name }),
    { headers: headers }
  );
  check(wsRes, { "workspace created": (r) => r.status === 201 });
  const workspaceId = wsRes.json("id");

  const threadRes = http.post(
    `${BASE_URL}/v1/workspaces/${workspaceId}/threads`,
    JSON.stringify({ title: "perf" }),
    { headers: headers }
  );
  check(threadRes, { "thread created": (r) => r.status === 201 });
  const threadId = threadRes.json("id");

  return { workspaceId: workspaceId, threadId: threadId };
}

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// JWT access tokens are short-lived (900s, config.py's jwt_access_ttl_
// seconds) — every script here logs in once per VU and holds that
// token for the VU's entire lifetime. That's invisible at smoke/nightly
// durations (30s-2m, nowhere near 900s) but a real, observed failure at
// soak duration (1h): ~75% of a real 1h soak run's requests came back
// 401 once the token aged past 15 minutes, caught on the very first
// real full-hour soak run against a tagged release (S12 v1.0.0), not
// assumed from reading the TTL config. login()'s Set-Cookie response
// puts the refresh token in k6's per-VU cookie jar automatically (same
// HttpOnly-cookie mechanism the real web app's authRefresh.ts uses) —
// POST /v1/auth/refresh needs no body, just that cookie already being
// present.
export function refreshAccessToken() {
  const res = http.post(`${BASE_URL}/v1/auth/refresh`, null, {
    headers: { "Content-Type": "application/json" },
  });
  check(res, { "token refresh succeeded": (r) => r.status === 200 });
  return res.json("access_token");
}

// Wraps a request-issuing closure (a function of the current token,
// returning a k6 http.Response) with transparent 401-triggered token
// refresh-and-retry — the response actually check()'d by the caller is
// always the final, post-refresh attempt, not the stale-token 401.
// `ctx` is the script's per-VU context object (e.g. `vuCtx`/`data`);
// its `.token` field is updated in place so every subsequent call
// picks up the refreshed token without the caller having to thread it
// through by hand.
export function withTokenRefresh(ctx, issueRequest) {
  let res = issueRequest(ctx.token);
  if (res.status === 401) {
    ctx.token = refreshAccessToken();
    res = issueRequest(ctx.token);
  }
  return res;
}

// S3/MinIO presigned POST forms require every policy-condition field to
// appear *before* the file field in the multipart body (the server
// processes fields as a stream and needs them already accumulated by
// the time it reaches the file part). k6's http.post(url, {plainObject})
// convenience form serializes via a Go map internally — Go map
// iteration order is randomized by design, so passing upload_fields as
// a plain object silently reorders them across runs, intermittently
// putting "file" before a required field and failing with MinIO's
// "MalformedPOSTRequest ... name of the uploaded key is missing" (this
// was a real, non-obvious bug caught while building this script — not
// a hypothetical). Building the multipart body as a raw string gives
// this the field ordering it actually needs.
export function uploadToPresignedS3Form(uploadUrl, fields, fileName, fileContent, fileContentType) {
  const boundary = "----k6FormBoundary" + Math.random().toString(16).slice(2);
  let body = "";
  for (const key in fields) {
    body += `--${boundary}\r\n`;
    body += `Content-Disposition: form-data; name="${key}"\r\n\r\n`;
    body += `${fields[key]}\r\n`;
  }
  body += `--${boundary}\r\n`;
  body += `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n`;
  body += `Content-Type: ${fileContentType}\r\n\r\n`;
  body += `${fileContent}\r\n`;
  body += `--${boundary}--\r\n`;

  return http.post(uploadUrl, body, {
    headers: { "Content-Type": `multipart/form-data; boundary=${boundary}` },
  });
}
