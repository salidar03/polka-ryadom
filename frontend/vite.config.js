import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// В режиме разработки запросы /api уходят на FastAPI (порт 8000).
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': process.env.API_URL || 'http://127.0.0.1:8000' },
  },
});
