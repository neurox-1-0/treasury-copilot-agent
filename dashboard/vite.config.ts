import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * dashboard/vite.config.ts
 * ========================
 *
 * Vite configuration for the HITL Approval Dashboard.
 *
 * Proxy rule
 * ----------
 * All requests from the React app to ``/api/*`` are forwarded to the
 * HITL FastAPI service running on port 8006.  This eliminates CORS issues
 * during development — the browser only ever talks to the Vite dev server
 * at localhost:5173.
 *
 * Test configuration
 * ------------------
 * Vitest is configured here so that ``npm run test`` works without a
 * separate ``vitest.config.ts``.  The ``jsdom`` environment simulates the
 * browser DOM for React Testing Library.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8006",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    css: true,
  },
});
