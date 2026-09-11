import { expect, test } from '@playwright/test';
import { mockApi } from '../e2e/fixtures/mockApi';

/**
 * Production navigation budget smoke test. These are deliberately loose so the
 * suite is a regression tripwire, not a flaky gate; the real numbers are also
 * attached to the report for before/after comparison.
 */

type HomeMetrics = {
  lcp: number;
  cls: number;
  navDuration: number;
  transferBytes: number;
};

async function installVitals(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    const perf = { lcp: 0, cls: 0 };
    (window as unknown as { __perf: typeof perf }).__perf = perf;
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) perf.lcp = entry.startTime;
      }).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as Array<{ hadRecentInput?: boolean; value?: number }>) {
          if (!entry.hadRecentInput) perf.cls += entry.value || 0;
        }
      }).observe({ type: 'layout-shift', buffered: true });
    } catch {
      // Older engines may not expose these entry types; fall back to zeros.
    }
  });
}

test.describe('homepage performance', () => {
  test('cold navigation stays within the LCP/CLS budget', async ({ page }, testInfo) => {
    await mockApi(page);
    await installVitals(page);

    await page.goto('/', { waitUntil: 'load' });
    await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
    await page.waitForTimeout(1200);

    const metrics = await page.evaluate<HomeMetrics>(() => {
      const perf = (window as unknown as { __perf: { lcp: number; cls: number } }).__perf;
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
      const transferBytes = performance
        .getEntriesByType('resource')
        .filter((entry) => (entry as PerformanceResourceTiming).initiatorType === 'script')
        .reduce((sum, entry) => sum + ((entry as PerformanceResourceTiming).transferSize || 0), 0);
      return {
        lcp: perf?.lcp || 0,
        cls: perf?.cls || 0,
        navDuration: nav?.duration || 0,
        transferBytes
      };
    });

    await testInfo.attach('home-metrics.json', {
      body: JSON.stringify(metrics, null, 2),
      contentType: 'application/json'
    });

    expect(metrics.lcp).toBeGreaterThan(0);
    expect(metrics.lcp).toBeLessThan(4000);
    expect(metrics.cls).toBeLessThan(0.2);
  });

  test('language switch stays interactive without a blank frame', async ({ page }) => {
    await mockApi(page, { language: 'en' });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();

    const started = Date.now();
    await page.getByRole('button', { name: 'Switch to Simplified Chinese' }).click();
    await expect(page.getByRole('heading', { name: '提示词', exact: true })).toBeVisible();
    const elapsed = Date.now() - started;

    expect(elapsed).toBeLessThan(3000);
  });
});
