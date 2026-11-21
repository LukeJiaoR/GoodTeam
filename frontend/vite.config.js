import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  test: {
    environment: 'jsdom',
    setupFiles: './src/vitest/setup.js',
    css: true,
    include: ['src/**/*.test.jsx', 'src/**/*.vitest.js'],
  },
  server: {
    host: '127.0.0.1',
    port: 8790,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8788',
    },
  },
});
