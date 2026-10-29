import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
      // Page scans, previews, and renders are served by the API under
      // /media — without this the dev server answers index.html (200) and
      // the viewer renders garbage instead of the upload.
      "/media": "http://localhost:8000",
    },
  },
});
