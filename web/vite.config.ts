import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버(5173)는 /api, /chat 요청을 그룹웨어·비서 서버(8100)로 넘긴다.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8100";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": backend, "/chat": backend },
  },
});
