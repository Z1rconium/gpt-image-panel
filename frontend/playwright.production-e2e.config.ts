import { defineConfig, devices } from '@playwright/test';

/** Run mask browser acceptance against the static bundle and emitted Worker. */
export default defineConfig({
  testDir: './tests/e2e',
  // Other e2e files exercise Vite-only source URLs; the normal suite owns them.
  testMatch: ['**/edit-mask.spec.ts'],
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4173',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 240_000
  },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
