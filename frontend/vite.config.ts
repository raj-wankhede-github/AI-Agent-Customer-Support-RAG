import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // The dev server proxies API calls so the browser talks to a single origin, which keeps
  // the HttpOnly session cookie first-party (same as the nginx setup in production).
  const apiTarget = env.VITE_API_PROXY_TARGET || "http://localhost:8000";
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target: apiTarget },
        "/health": { target: apiTarget },
        "/ready": { target: apiTarget },
      },
    },
    build: { sourcemap: false, target: "es2022" },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});
