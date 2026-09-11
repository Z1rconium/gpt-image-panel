import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      '$app/environment': fileURLToPath(new URL('./tests/unit/stubs/app-environment.ts', import.meta.url)),
      $lib: fileURLToPath(new URL('./src/lib', import.meta.url))
    }
  },
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node'
  }
});
