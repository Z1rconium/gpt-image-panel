import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { visualizer } from 'rollup-plugin-visualizer';

export default defineConfig({
  plugins: [
    sveltekit(),
    ...(process.env.ANALYZE ? [visualizer({ filename: 'stats.html', gzipSize: true, open: false })] : [])
  ],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9090',
        changeOrigin: false,
        xfwd: true
      },
      '/health': {
        target: 'http://127.0.0.1:9090',
        changeOrigin: false,
        xfwd: true
      }
    }
  }
});
