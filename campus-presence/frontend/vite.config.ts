import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  // `npm run build` output is served by the backend under /ui/; the dev server runs at the root.
  // A static host that serves the app from its own root (Vercel) builds with VITE_BASE=/.
  base: process.env.VITE_BASE ?? (command === 'build' ? '/ui/' : '/'),
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        rewrite: (path) => path,
      },
    },
  },
}))
