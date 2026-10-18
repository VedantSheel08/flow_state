import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.FLOWSTATE_LAB_API ?? 'http://127.0.0.1:8015'

export default defineConfig({
  plugins: [react()],
  css: { postcss: { plugins: [] } },
  server: {
    port: 5176,
    strictPort: true,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend.replace('http', 'ws'), ws: true },
    },
  },
  preview: {
    port: 5176,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend.replace('http', 'ws'), ws: true },
    },
  },
})
