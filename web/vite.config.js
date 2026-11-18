import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // 5173 留给字典应用前端，erp-lite 开发端口固定 15173，被占时直接报错不漂移
    port: Number(process.env.VITE_PORT) || 15173,
    strictPort: true,
    proxy: {
      '/api': {
        // 本机 8000 被其他服务占用（ask-gateway），erp-lite 后端固定 8100；VITE_API_TARGET 仅作临时覆盖
        target: process.env.VITE_API_TARGET || 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },
})
