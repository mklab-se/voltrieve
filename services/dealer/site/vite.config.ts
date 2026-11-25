import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  // The service serves the page, so every call goes to the same origin. Here a proxy does: to the deployed
  // service with DEALER_URL (`just dealer`), otherwise to the mock.
  const target = loadEnv(mode, ".", "DEALER_").DEALER_URL || "http://127.0.0.1:8790";
  const proxy = { target, changeOrigin: true };
  return {
    plugins: [react()],
    server: { port: 5176, strictPort: true, proxy: { "/staff": proxy, "/config.json": proxy } },
    test: { include: ["src/**/*.test.{ts,tsx}"], environment: "node" },
  };
});
