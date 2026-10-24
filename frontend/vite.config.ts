import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.SN_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: api, changeOrigin: true } } },
  preview: { port: 4173, proxy: { "/api": { target: api, changeOrigin: true } } },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1600,
    rollupOptions: { output: { manualChunks: { echarts: ["echarts"], leaflet: ["leaflet", "react-leaflet"] } } },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
});
