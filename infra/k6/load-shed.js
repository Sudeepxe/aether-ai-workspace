// S11 #119: real load-shed verification (§10.5/§10.8's "soak + load-shed
// verification" line). This does NOT test whether the app is fast — it
// tests what happens once it's deliberately pushed past capacity: does
// it fail *closed and cheap* (a fast 429 with Retry-After, §3.6.3's
// token-bucket limiter) for the offending identity, without degrading
// service for anyone else sharing the same process/pool/Redis instance.
//
// Two scenarios run concurrently against the same live stack:
//
//  - "abuser": a handful of VUs sharing ONE real user's token, hammering
//    the HEAVY-class chat-send route (30 req/60s budget, §3.6.3) as fast
//    as possible with no pacing sleep — deliberately exceeds its own
//    budget within seconds.
//  - "bystander": several other real, separately-registered users making
//    ordinary CHEAP-class reads (GET /v1/me) at a normal pace for the
//    same window.
//
// Pass criteria: the abuser sees real 429s (with Retry-After) once its
// bucket empties — proof the limiter actually sheds, not just the unit
// test against a fake clock — AND the bystanders' success rate/latency
// stay within the same budgets non-ai-crud.js enforces on an otherwise
// idle stack. If shedding one identity ever meant blocking the shared
// Redis connection, exhausting the DB pool, or starving other event-loop
// work, the bystanders' numbers would visibly degrade here — that's
// the isolation this script exists to catch, not assume.
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL, registerAndLogin, authHeaders, staggerVuStart } from "./lib/setup.js";

// Deliberately NOT named K6_VUS/K6_DURATION: k6 recognizes those two
// env var names itself and uses them to synthesize a simple top-level
// vus/duration config that silently overrides an explicit `scenarios`
// block entirely (a real "env level configuration overrode scenarios
// configuration entirely" warning, caught while building this script,
// not a hypothetical) — this script needs two independently-sized VU
// pools, which simple config can't express.
const DURATION = __ENV.K6_SHED_DURATION || "45s";

export const options = {
  scenarios: {
    abuser: {
      executor: "constant-vus",
      exec: "abuse",
      vus: Number(__ENV.K6_ABUSER_VUS) || 3,
      duration: DURATION,
    },
    bystander: {
      executor: "constant-vus",
      exec: "bystand",
      vus: Number(__ENV.K6_BYSTANDER_VUS) || 5,
      duration: DURATION,
    },
  },
  thresholds: {
    // The abuser MUST get shed — no 429s here would mean the limiter
    // isn't actually enforcing the HEAVY budget under real concurrency.
    "checks{scenario:abuser}": ["rate>0.99"], // "got a 200 or a real 429", not "always 200"
    "shed_events": ["count>0"],
    // Bystanders must be unaffected by the abuser sharing the same
    // stack — same latency/success bar non-ai-crud.js holds on an
    // otherwise-idle instance.
    "http_req_duration{scenario:bystander}": ["p(95)<300"],
    "checks{scenario:bystander}": ["rate>0.99"],
  },
};

import { Counter } from "k6/metrics";
const shedEvents = new Counter("shed_events");

// setup() runs once, globally — deliberate here (unlike the other
// scripts' per-VU lazy login): the abuser scenario's whole point is
// multiple VUs sharing ONE identity's rate-limit bucket, so the token
// has to be created once and handed to every abuser VU via k6's
// setup()-return-value sharing mechanism.
export function setup() {
  const abuserToken = registerAndLogin("k6-abuser");
  const headers = authHeaders(abuserToken);
  const wsRes = http.post(
    `${BASE_URL}/v1/workspaces`,
    JSON.stringify({ name: "k6 load-shed" }),
    { headers: headers }
  );
  const workspaceId = wsRes.json("id");
  const threadRes = http.post(
    `${BASE_URL}/v1/workspaces/${workspaceId}/threads`,
    JSON.stringify({ title: "load-shed" }),
    { headers: headers }
  );
  const threadId = threadRes.json("id");
  return { abuserToken: abuserToken, workspaceId: workspaceId, threadId: threadId };
}

// The shared abuser identity hammers the HEAVY-class send-message route
// with no pacing — it WANTS to blow through the 30/60s budget fast.
// Both 200 (still under budget) and 429 (correctly shed) count as a
// "working as designed" outcome; anything else (500, timeout) doesn't.
export function abuse(data) {
  const url = `${BASE_URL}/v1/workspaces/${data.workspaceId}/threads/${data.threadId}/messages`;
  const body = JSON.stringify({
    content: "load-shed probe",
    client_message_id: `k6-shed-${__VU}-${__ITER}-${Date.now()}`,
  });
  const headers = authHeaders(data.abuserToken);
  headers["Accept"] = "text/event-stream";
  const res = http.post(url, body, { headers: headers, tags: { name: "abuse" } });
  const ok = check(res, {
    "200 (served) or 429 (correctly shed)": (r) => r.status === 200 || r.status === 429,
  });
  if (res.status === 429) {
    shedEvents.add(1);
    check(res, { "429 carries Retry-After": (r) => Number(r.headers["Retry-After"]) > 0 });
  }
  if (!ok) {
    console.error(`abuser got unexpected status ${res.status}: ${res.body}`);
  }
  // No sleep — deliberately saturating.
}

// Each bystander VU is its own real, separately-registered user — an
// entirely different rate-limit identity from the abuser's — making
// ordinary paced CHEAP-class reads throughout the same window.
let bystanderToken = null;

export function bystand() {
  if (bystanderToken === null) {
    staggerVuStart();
    bystanderToken = registerAndLogin("k6-bystander");
  }
  const res = http.get(`${BASE_URL}/v1/me`, {
    headers: authHeaders(bystanderToken),
    tags: { name: "me" },
  });
  check(res, { "200 OK": (r) => r.status === 200 });
  sleep(1);
}
