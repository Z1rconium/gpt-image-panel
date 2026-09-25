import { defineConfig, devices } from '@playwright/test';

const devPort = Number(process.env.PLAYWRIGHT_DEV_PORT || 5173);

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: {
    timeout: 5_000
  },
  fullyParallel: true,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${devPort}`,
    url: `http://127.0.0.1:${devPort}`,
    reuseExistingServer: !process.env.CI
  },
  use: {
    baseURL: `http://127.0.0.1:${devPort}`,
    trace: 'retain-on-failure'
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] }
    }
  ]
});
