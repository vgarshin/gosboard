import { defineConfig } from 'vite';
export default defineConfig({server: {host: '127.0.0.1', port: 5173, strictPort: true, proxy: {'/api': process.env.API_PROXY || 'http://127.0.0.1:8011'}}});
