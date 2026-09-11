import { defineConfig, devices } from '@playwright/test';

/**
 * Production performance/visual config. Unlike the functional suite it serves
 * the built output through `vite preview` so measurements reflect the real
 * bundle, and it fixes viewports across desktop and mobile.
 *
 * Run with: npm --prefix frontend run test:perf:production
 * Update screenshot baselines with: npm --prefix frontend run test:perf:production -- --update-snapshots
 */
export default defineConfig({
  testDir: './tests/perf',
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  webServer: {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 240_000
  },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure'
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } }
    }
  ]
});
