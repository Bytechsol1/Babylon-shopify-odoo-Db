import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build goes straight into the Python app's static folder, so `uvicorn` serves it and
// nobody needs Node just to run the station. `npm run dev` proxies the API to the Python server.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../app/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
