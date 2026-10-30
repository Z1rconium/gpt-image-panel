import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';
import { visualizer } from 'rollup-plugin-visualizer';

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit({
      adapter: adapter({ fallback: 'index.html' }),
      preprocess: vitePreprocess(),
      alias: { $lib: 'src/lib' }
    }),
    ...(process.env.ANALYZE ? [visualizer({ filename: 'stats.html', gzipSize: true, open: false })] : [])
  ],
  build: {
    cssMinify: 'lightningcss',
    cssTarget: ['chrome111', 'safari16.4', 'firefox128']
  },
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
