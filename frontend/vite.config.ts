import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the package's static directory: `vgc web` serves whatever is there, so a
// built app needs no extra wiring and no second server in production.
export default defineConfig({
  // FastAPI serves this directory at /static, so built asset URLs must carry that prefix.
  base: "/static/",
  plugins: [react()],
  build: { outDir: "../src/vgc/web/static", emptyOutDir: true },
  // `npm run dev` gives hot reload while the Python API keeps serving the real endpoints.
  server: { proxy: { "/api": "http://127.0.0.1:8001" } },
});
