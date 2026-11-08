import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // Stable vendor chunks: tab switches and deploys re-download only
        // the chunks that changed; three.js (~700KB) stays in its own
        // lazy chunk and never blocks first paint.
        manualChunks: {
          "react-vendor": ["react", "react-dom"],
          "three-vendor": ["three"],
          "anim-vendor": ["animejs", "lenis", "perfect-freehand"],
        },
      },
    },
  },
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
