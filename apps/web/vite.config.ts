import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Sprint 0: minimal config. Bundle-size budget gate (ADR-5.1, 250 KB gz)
// is wired in CI when the first real route lands (S3).
export default defineConfig({
  plugins: [react()],
});
