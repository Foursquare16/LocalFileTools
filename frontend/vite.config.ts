import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the API runs on uvicorn at :8080. The Host header is kept, so the backend's Origin check passes.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
});
