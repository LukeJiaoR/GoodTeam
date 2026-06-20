import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    host: '127.0.0.1',
    port: 8790,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8788',
      '/start': 'http://127.0.0.1:8788',
      '/approve': 'http://127.0.0.1:8788',
      '/artifact': 'http://127.0.0.1:8788',
      '/terminate': 'http://127.0.0.1:8788',
      '/project-delete': 'http://127.0.0.1:8788',
      '/retry': 'http://127.0.0.1:8788',
      '/open-path': 'http://127.0.0.1:8788',
      '/project-config': 'http://127.0.0.1:8788',
    }
  }
})
