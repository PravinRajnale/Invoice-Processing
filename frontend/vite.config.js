import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // SSE needs buffering disabled end to end, otherwise the live check
      // stream arrives as one lump when the pipeline finishes.
      '/api': { target: 'http://localhost:4000', changeOrigin: true },
    },
  },
});
