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
