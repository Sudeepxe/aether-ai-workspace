// NFR-P-1: "Grounded chat p95 time-to-first-token < 1.5 s" (S9 #98,
// ADR-2.4). Uses a real ingested document (real presigned MinIO
// upload, real worker-side processing to `ready`) so Gate 1 (ADR-6.4)
// actually clears and generation runs — the same real-pipeline
// requirement test_grounded_chat_e2e.py's integration test has.
//
// TTFT approximation note: k6's http module reads the full response
// body before returning (no true incremental-stream API without the
// experimental k6/experimental/streams module, not used here to keep
// this script simple) — `res.timings.waiting` (time-to-first-byte)
// approximates TTFT well for this SSE endpoint specifically, since the
// `meta` event is always the very first thing written to the response
// (§4.4's grammar), so TTFB and "time until the stream starts" are
// effectively the same instant here.
import http from "k6/http";
import { check, sleep } from "k6";
import crypto from "k6/crypto";
import {
  BASE_URL,
  registerAndLogin,
  createWorkspaceAndThread,
  authHeaders,
  uploadToPresignedS3Form,
  staggerVuStart,
  withTokenRefresh,
} from "./lib/setup.js";

// Same script serves the PR-smoke (default: 2 VUs, 40s) and the
// pre-release soak run (§10.5: "1h at envelope concurrency") — soak
// with `K6_VUS=20 K6_DURATION=1h k6 run grounded-chat.js`, not a
// separate duplicated script. A lower default VU count than the other
// two scripts: each VU's first iteration does a *real* document
// ingestion (malware scan + parse + chunk + embed) against a single,
// non-concurrent worker consumer loop — 5 simultaneous real ingestions
// against one worker process serialize and can blow the poll timeout
// below, an ingestion-throughput constraint this script isn't trying
// to budget, not a bug in the chat-latency measurement itself.
export const options = {
  scenarios: {
    smoke: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_VUS) || 2,
      duration: __ENV.K6_DURATION || "40s",
      gracefulStop: "90s", // real per-VU document ingestion can take up to ~60s to poll to ready
    },
  },
  thresholds: {
    "http_req_waiting{name:chat}": ["p(95)<1500"],
    checks: ["rate>0.99"],
  },
};

const DOCUMENT_CONTENT =
  "# Chaos Corp Support Policy\n\n" +
  "Chaos Corp offers a 45-day money-back guarantee on all annual subscriptions.\n";

function ingestDocument(token, workspaceId) {
  const headers = authHeaders(token);
  const hash = crypto.sha256(DOCUMENT_CONTENT, "hex");

  const initRes = http.post(
    `${BASE_URL}/v1/workspaces/${workspaceId}/documents:initiate`,
    JSON.stringify({
      filename: "k6.md",
      mime: "text/markdown",
      size_bytes: DOCUMENT_CONTENT.length,
      content_sha256: hash,
    }),
    { headers: headers }
  );
  check(initRes, { "upload initiated": (r) => r.status === 200 });
  const init = initRes.json();

  const uploadRes = uploadToPresignedS3Form(
    init.upload_url,
    init.upload_fields,
    "k6.md",
    DOCUMENT_CONTENT,
    "text/markdown"
  );
  check(uploadRes, { "presigned upload accepted": (r) => r.status === 200 || r.status === 204 });

  const confirmRes = http.post(
    `${BASE_URL}/v1/workspaces/${workspaceId}/documents:confirm`,
    JSON.stringify({
      document_id: init.document_id,
      object_key: init.object_key,
      filename: "k6.md",
      mime: "text/markdown",
      size_bytes: DOCUMENT_CONTENT.length,
      content_sha256: hash,
    }),
    { headers: headers }
  );
  check(confirmRes, { "upload confirmed": (r) => r.status === 201 });

  // Real worker processing (malware scan -> parse -> chunk -> embed)
  // takes real wall-clock time — poll rather than assume.
  for (let i = 0; i < 60; i++) {
    const statusRes = http.get(
      `${BASE_URL}/v1/workspaces/${workspaceId}/documents/${init.document_id}`,
      { headers: headers }
    );
    const doc = statusRes.json();
    if (doc.status === "ready") {
      return;
    }
    if (doc.status === "failed") {
      throw new Error(`k6 fixture document failed to ingest: ${doc.failure_reason}`);
    }
    sleep(1);
  }
  throw new Error("k6 fixture document never reached ready within 60s");
}

// Module-scope state is per-VU in k6 — a lazy one-time-per-VU setup
// (own user, own workspace, own ingested document), not k6's `setup()`
// hook (which runs exactly once *globally* and would make every VU
// share one user's rate-limit budget, tripping real 429s under
// concurrent load and measuring the rate limiter instead of NFR-P-1 —
// caught empirically, not assumed, while building this script).
let vuCtx = null;

function ensureVuContext() {
  if (vuCtx === null) {
    staggerVuStart();
    const logged = registerAndLogin("k6-grounded");
    const created = createWorkspaceAndThread(logged.token, "k6 grounded");
    ingestDocument(logged.token, created.workspaceId);
    vuCtx = {
      token: logged.token,
      refreshCookie: logged.refreshCookie,
      workspaceId: created.workspaceId,
      threadId: created.threadId,
    };
  }
  return vuCtx;
}

export default function () {
  const data = ensureVuContext();
  const url = `${BASE_URL}/v1/workspaces/${data.workspaceId}/threads/${data.threadId}/messages`;
  const body = JSON.stringify({
    content: "What is Chaos Corp's money-back guarantee period?",
    client_message_id: `k6-${__VU}-${__ITER}-${Date.now()}`,
  });
  // withTokenRefresh (lib/setup.js): the access token ages past its
  // real 900s TTL over a 1h soak run — invisible at this script's own
  // short smoke default, a real ~75% failure rate at soak duration
  // (caught on the first real 1h run against a tagged release).
  const res = withTokenRefresh(data, (token) => {
    const headers = authHeaders(token);
    headers["Accept"] = "text/event-stream";
    return http.post(url, body, { headers: headers, tags: { name: "chat" } });
  });
  check(res, {
    "200 OK": (r) => r.status === 200,
    // Whitespace-tolerant: FastAPI's default json.dumps separators
    // include a space after the colon ("grounded": true), which a
    // literal '"grounded":true' substring check misses entirely — a
    // real false-negative caught while building this script, not a
    // hypothetical.
    grounded: (r) => /"grounded":\s*true/.test(r.body),
  });
  // Paced to stay under the real HEAVY-class rate limit (30/60s per
  // user, §3.6.3) — this budgets *latency*, not throughput.
  sleep(3);
}
