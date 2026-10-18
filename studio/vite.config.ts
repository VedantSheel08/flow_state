import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Studio talks to its own backend instance (default :8017) so its live session never replaces the one the lab UI uses on :8015.
const backend = process.env.FLOWSTATE_STUDIO_API ?? 'http://127.0.0.1:8017'

export default defineConfig({
  plugins: [react()],
  css: { postcss: { plugins: [] } },
  server: { port: 5178, strictPort: true, proxy: { '/api': { target: backend, changeOrigin: true }, '/ws': { target: backend.replace('http', 'ws'), ws: true } } },
  preview: { port: 5178, proxy: { '/api': { target: backend, changeOrigin: true }, '/ws': { target: backend.replace('http', 'ws'), ws: true } } },
})
