import { defineConfig, devices } from "@playwright/test";

// Media generation, not a CI gate (S12 #125) — deliberately separate
// from playwright.config.ts's testDir/CI wiring: this config exists
// only to produce a real recorded video of the golden path for the
// README's demo GIF, run on demand via `make demo-gif`, never in the
// PR/CI lane. A small viewport keeps the resulting GIF README-sized.
export default defineConfig({
  testDir: "./e2e-demo",
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    video: "on",
    viewport: { width: 960, height: 640 },
  },
  outputDir: "./demo-output",
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env["CI"],
    timeout: 30_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
