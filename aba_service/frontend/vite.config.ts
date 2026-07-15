import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import tsConfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [
    // Must run before the React plugin so generated routes get transformed.
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    tsConfigPaths(),
  ],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    proxy: {
      // All backend APIs are called under the same-origin `/api` prefix so they
      // are easy to tell apart from page routes. In dev we forward `/api` to the
      // FastAPI backend; in production nginx proxies `/api` -> :8010.
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
        ws: true,
      },
      // The Ollama API is the one exception — it keeps its own `/ollama` prefix.
      "/ollama": {
        target: "http://127.0.0.1:11434",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/ollama/, ""),
      },
    },
  },
});
