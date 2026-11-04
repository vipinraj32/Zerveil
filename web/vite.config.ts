import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: the React app runs on :5173 and proxies the platform server on :8000.
// Build: output goes where FastAPI serves it (src/zerveil/api/static).
const API = process.env.ZERVEIL_API_URL ?? "http://localhost:8000";
const proxied = ["/api", "/auth", "/a2a", "/mcp", "/webhooks", "/checkout"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxied.map((path) => [path, { target: API, changeOrigin: false }])),
  },
  build: {
    outDir: "../src/zerveil/api/static",
    emptyOutDir: true,
  },
});
