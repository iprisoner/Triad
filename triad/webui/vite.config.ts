import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// v2.3.1: 从环境变量读取 API 地址，支持远程部署
const API_HOST = process.env.TRIAD_API_HOST || 'localhost'
const API_PORT = process.env.TRIAD_API_PORT || '18080'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    host: true,  // 允许局域网访问
    proxy: {
      '/api': `http://${API_HOST}:${API_PORT}`,
      '/ws': { target: `ws://${API_HOST}:${API_PORT}`, ws: true },
    },
  },
})