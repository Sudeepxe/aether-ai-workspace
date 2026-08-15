import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Bundle-size budget gate (ADR-5.1, 250 KB gz) lands as a CI check
// alongside route-level code splitting once there's enough surface
// (markdown/highlight/admin bundles) for splitting to matter (S6+).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Dev-only convenience so the SPA can call relative /v1/... paths
    // without CORS — in prod, the reverse proxy unifies the origin
    // (ADR-3.7), which this mirrors.
    proxy: {
      "/v1": { target: "http://localhost:8000", changeOrigin: true },
      "/healthz": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
