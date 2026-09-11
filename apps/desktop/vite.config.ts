/// <reference types="vitest" />
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  resolve: {
    // Mirrors the `paths` entry in tsconfig.json. Both are needed: tsc reads one, the
    // bundler reads the other, and a type-check that passes while the build fails is
    // exactly the failure this pairing prevents.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 1420,
    strictPort: true,
    // Talk to the core in dev without CORS. The packaged app has no dev server, so it
    // addresses the API directly — see src/lib/origin.ts.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: true },
    },
  },
  build: { target: "esnext", sourcemap: true },
  // The parts of the interface worth testing are the ones that decide what a person is
  // shown: which face goes with which mood, and what text ends up on the screen. Those are
  // plain functions and components, and they run here rather than being eyeballed.
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    globals: true,
    css: false,
  },
});
