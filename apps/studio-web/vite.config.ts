import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Dev API port (scripts/dev.ps1 -ApiPort). :8000 is kept clear:
      // a stale pre-reload worker can squat it with old routes.
      "/api": "http://localhost:8001",
      "/health": "http://localhost:8001",
      // Page scans, previews, and renders are served by the API under
      // /media — without this the dev server answers index.html (200) and
      // the viewer renders garbage instead of the upload.
      "/media": "http://localhost:8001",
    },
  },
});
