import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Locally the build goes straight into the Python app's static folder, so `uvicorn` serves it and nobody
// needs Node to run the station. On Vercel (which sets VERCEL=1) it builds to dist/ and Vercel serves it,
// forwarding /api to the backend (see vercel.json). `npm run dev` proxies the API to the Python server.
export default defineConfig({
  plugins: [react()],
  build: { outDir: process.env.VERCEL ? "dist" : "../app/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
