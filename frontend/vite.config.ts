import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

const backend = process.env.FLOWSTATE_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Do not search parent directories for a postcss config.
  css: { postcss: { plugins: [] } },
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend.replace('http', 'ws'), ws: true },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend.replace('http', 'ws'), ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
})
