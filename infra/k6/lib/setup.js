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

export function registerAndLogin(emailPrefix) {
  const email = `${emailPrefix}-${__VU}-${Date.now()}@example.com`;
  const password = "s3cret!!";
  http.post(
    `${BASE_URL}/v1/auth/register`,
    JSON.stringify({ email: email, password: password, display_name: email }),
    { headers: { "Content-Type": "application/json" } }
  );
  const loginRes = http.post(
    `${BASE_URL}/v1/auth/login`,
    JSON.stringify({ email: email, password: password }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(loginRes, { "login succeeded": (r) => r.status === 200 });
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
