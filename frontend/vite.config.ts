import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: mode === "desktop" ? 5174 : 5173,
    strictPort: mode === "desktop",
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET || "http://127.0.0.1:8000",
      },
      "/health": {
        target: process.env.API_PROXY_TARGET || "http://127.0.0.1:8000",
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    ...(mode === "desktop"
      ? { outDir: "../desktop/dist", emptyOutDir: true }
      : {}),
  },
}));
