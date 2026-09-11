import { expect, test } from '@playwright/test';
import { mockApi } from '../e2e/fixtures/mockApi';

/**
 * Opt-in visual baselines for the key surfaces (desktop/mobile, light/dark).
 * Baselines are generated locally and are not required to be committed:
 *
 *   RUN_VISUAL_TESTS=true npm --prefix frontend run test:perf:production
 *   RUN_VISUAL_TESTS=true npm --prefix frontend run test:perf:production -- --update-snapshots
 */
const enabled = process.env.RUN_VISUAL_TESTS === 'true';

test.describe('visual baselines', () => {
  test.skip(!enabled, 'set RUN_VISUAL_TESTS=true to capture visual baselines');

  for (const scheme of ['dark', 'light'] as const) {
    test(`home ${scheme}`, async ({ page }, testInfo) => {
      await mockApi(page, { language: 'en' });
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
      await page.waitForTimeout(400);
      await expect(page).toHaveScreenshot(`home-${scheme}-${testInfo.project.name}.png`, {
        fullPage: true,
        animations: 'disabled'
      });
    });
  }
});
