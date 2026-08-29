// NFR-P-1: "ungrounded < 800 ms" (S9 #98, ADR-2.4).
//
// Honest mapping note: since S6 (grounded chat), *every* chat turn runs
// real hybrid retrieval before generation — a workspace with zero
// ingested documents always clears zero chunks, so Gate 1 (ADR-6.4)
// refuses before the generator is ever called
// (test_gate_1_refusal_short_circuits_before_any_generator_call).
// There is no code path left that performs a genuine ungrounded LLM
// call. This script measures that refusal fast-path instead — the
// closest real analogue to "ungrounded" latency this architecture still
// has, and arguably the more useful number to budget (it's the floor
// every chat request pays before any real generation work starts).
import http from "k6/http";
import { check, sleep } from "k6";
import {
  BASE_URL,
  registerAndLogin,
  createWorkspaceAndThread,
  authHeaders,
  staggerVuStart,
  withTokenRefresh,
} from "./lib/setup.js";

export const options = {
  scenarios: {
    smoke: {
      executor: "constant-vus",
      vus: Number(__ENV.K6_VUS) || 5,
      duration: __ENV.K6_DURATION || "30s",
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<800"],
    checks: ["rate>0.99"],
  },
};

// Module-scope state is per-VU in k6 — a lazy one-time-per-VU setup, not
// k6's `setup()` hook (which runs exactly once *globally* and would
// make every VU share one user's rate-limit budget, tripping real 429s
// under concurrent load and measuring the rate limiter instead of
// NFR-P-1 — this was caught empirically, not assumed, while building
// this script).
let vuCtx = null;

function ensureVuContext() {
  if (vuCtx === null) {
    staggerVuStart();
    const logged = registerAndLogin("k6-ungrounded");
    const created = createWorkspaceAndThread(logged.token, "k6 ungrounded");
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
    content: "What is the answer?",
    client_message_id: `k6-${__VU}-${__ITER}-${Date.now()}`,
  });
  // withTokenRefresh (lib/setup.js): the access token ages past its
  // real 900s TTL over a 1h soak run — invisible at this script's own
  // short smoke default, a real ~75% failure rate at soak duration
  // (caught on the first real 1h run against a tagged release).
  const res = withTokenRefresh(data, (token) => {
    const headers = authHeaders(token);
    headers["Accept"] = "text/event-stream";
    return http.post(url, body, { headers: headers });
  });
  check(res, { "200 OK": (r) => r.status === 200 });
  // Paced to stay under the real HEAVY-class rate limit (30/60s per
  // user, §3.6.3) — this budgets *latency*, not throughput.
  sleep(3);
}
