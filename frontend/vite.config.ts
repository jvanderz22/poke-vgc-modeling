import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the package's static directory: `vgc web` serves whatever is there, so a
// built app needs no extra wiring and no second server in production.
export default defineConfig(({ command }) => ({
  // Built assets are served by FastAPI out of /static, so they must carry that prefix. The dev
  // server has to stay at the root instead: the app routes on real paths (/endgames/<id>/9), and
  // under a /static/ base Vite would serve the app there and 404 every route beside it.
  base: command === "build" ? "/static/" : "/",
  plugins: [react()],
  build: { outDir: "../src/vgc/web/static", emptyOutDir: true },
  // `npm run dev` gives hot reload while the Python API keeps serving the real endpoints.
  server: { proxy: { "/api": "http://127.0.0.1:8001" } },
}));
