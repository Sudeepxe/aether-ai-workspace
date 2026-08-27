import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    // e2e/ and e2e-demo/ hold Playwright specs (npm run e2e; S12 #125's
    // demo recording), not Vitest ones — the two runners' `test()`
    // globals collide if Vitest also collects them.
    exclude: ["node_modules/**", "e2e/**", "e2e-demo/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary"],
    },
  },
});
