// NFR-P-3: "Non-AI API endpoints p95 < 200 ms" (S9 #98, ADR-2.4).
// GET /v1/me — the cheapest real authenticated read in the API, no DB
// join beyond the caller's own row. A representative non-AI CRUD path,
// not the AI-facing chat/generation routes budgeted separately.
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL, registerAndLogin, authHeaders } from "./lib/setup.js";

export const options = {
  scenarios: {
    smoke: {
      executor: "constant-vus",
      vus: 5,
      duration: __ENV.K6_DURATION || "20s",
    },
  },
  thresholds: {
    // NFR-P-3's literal budget. A shared, unpinned CI runner is not
    // "pinned hardware" (§10.7's nightly-budget premise) — this is an
    // honest, documented gap (no dedicated perf runner exists in this
    // environment, same class of deferral as the S11 VPS), so a CI
    // failure here is still a real regression signal even though the
    // absolute number carries more noise than a real pinned runner would.
    http_req_duration: ["p(95)<200"],
    checks: ["rate>0.99"],
  },
};

// Module-scope state is per-VU in k6 (each VU runs its own JS isolate)
// — this is a lazy one-time-per-VU login, not k6's `setup()` hook
// (which runs exactly once *globally* and would make every VU share
// one user's rate-limit budget, tripping real 429s under concurrent
// load and measuring the rate limiter instead of NFR-P-3).
let vuToken = null;

function ensureLoggedIn() {
  if (vuToken === null) {
    vuToken = registerAndLogin("k6-crud");
  }
  return vuToken;
}

export default function () {
  const token = ensureLoggedIn();
  const res = http.get(`${BASE_URL}/v1/me`, { headers: authHeaders(token) });
  check(res, { "200 OK": (r) => r.status === 200 });
  // Paced to stay under the real CHEAP-class rate limit (120/60s per
  // user, §3.6.3) — this budgets *latency*, not throughput; hammering
  // fast enough to trip 429s would measure the rate limiter, not NFR-P-3.
  sleep(1);
}
