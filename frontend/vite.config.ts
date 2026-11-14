import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the API runs on uvicorn at :8080. Logging in does not work through this proxy: it does not keep the
// Host header the backend's Origin check compares against. See CONTRIBUTING.md for the setup that works.
export default defineConfig({
  plugins: [react()],
  // fs.allow: the licences page imports NOTICE from the repo root, outside this folder.
  server: { fs: { allow: [".."] }, proxy: { "/api": "http://127.0.0.1:8080" } },
});
