import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The SPA is served by FastAPI from backend/app/static in the container image,
// so the build output lands there directly.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
