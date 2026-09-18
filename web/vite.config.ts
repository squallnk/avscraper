import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

// 开发时把 /api 代理到后端；生产由后端静态托管 dist
export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': resolve(__dirname, 'src') } },
  server: {
    host: '127.0.0.1',
    port: 5273,
    proxy: {
      '/api': { target: 'http://127.0.0.1:9300', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
