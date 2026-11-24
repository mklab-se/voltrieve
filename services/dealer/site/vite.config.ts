import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The service serves the page, so every call goes to the same origin. Here the mock answers instead.
const mock = "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [react()],
  server: { port: 5176, strictPort: true, proxy: { "/staff": mock, "/config.json": mock } },
  test: { include: ["src/**/*.test.{ts,tsx}"], environment: "node" },
});
