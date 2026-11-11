import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend(FastAPI)에 CORS 설정이 없어 개발 서버가 /api 요청을 Backend로 넘긴다.
const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true, ws: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
});
