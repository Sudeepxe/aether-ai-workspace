import { defineConfig, devices } from "@playwright/test";

// Assumes the full stack (Postgres, Redis, migrated API on :8000) is
// already running — infra/compose/compose.yml's dev+app profiles, or
// `make dev && make migrate && make run-api` locally. This config only
// owns the frontend half (Vite dev server, proxying /v1 to :8000 per
// vite.config.ts) so the same command works identically in CI and
// locally.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // Serialized in CI: every spec here drives one real, single-process
  // API+worker (not per-test isolated infra) plus a real, IP-keyed AUTH
  // rate limiter (10 req/60s, §7.5). Running spec files across parallel
  // workers (Playwright's default) let a heavy real spec (upload/
  // ClamAV-scan/ingest) starve a concurrent lightweight one on that
  // shared process, and let concurrent register/login flows collide on
  // the shared AUTH bucket — both real, observed CI flakes, not product
  // bugs. Local dev keeps the default worker count for fast iteration.
  workers: process.env["CI"] ? 1 : undefined,
  retries: process.env["CI"] ? 1 : 0,
  reporter: process.env["CI"] ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env["CI"],
    timeout: 30_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
