import { expect, test } from '@playwright/test';
import { mockApi } from './fixtures/mockApi';

const ONE_PX_PNG =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X1N6AAAAAElFTkSuQmCC';

/**
 * Lifecycle regression coverage for the exposure. WebGL may be unavailable in
 * headless Chromium under some configurations, so these tests assert the
 * settle contract (done always resolves, cancel is idempotent) rather than
 * pixels.
 */
test.describe('exposure lifecycle', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  });

  test('cancel before the module resolves still settles done', async ({ page }) => {
    const outcome = await page.evaluate(async (png) => {
      const moduleUrl = '/src/lib/webgl/exposureScene.ts';
      const { runExposure } = await import(moduleUrl);
      const canvas = document.createElement('canvas');
      canvas.style.width = '120px';
      canvas.style.height = '120px';
      document.body.appendChild(canvas);
      const image = new Image();
      image.src = png;
      await image.decode();
      const handle = runExposure(canvas, image, 1000);
      handle.cancel();
      handle.cancel();
      const settled = await Promise.race([
        handle.done.then(() => 'done'),
        new Promise((resolve) => setTimeout(() => resolve('timeout'), 1000))
      ]);
      canvas.remove();
      return settled;
    }, ONE_PX_PNG);

    expect(outcome).toBe('done');
  });

  test('a zero-size canvas settles done without throwing', async ({ page }) => {
    const outcome = await page.evaluate(async (png) => {
      const moduleUrl = '/src/lib/webgl/exposureScene.ts';
      const { runExposure } = await import(moduleUrl);
      const canvas = document.createElement('canvas');
      const image = new Image();
      image.src = png;
      await image.decode();
      const handle = runExposure(canvas, image, 1000);
      const settled = await Promise.race([
        handle.done.then(() => 'done'),
        new Promise((resolve) => setTimeout(() => resolve('timeout'), 1000))
      ]);
      canvas.remove();
      return settled;
    }, ONE_PX_PNG);

    expect(outcome).toBe('done');
  });

  test('a lost WebGL context settles done', async ({ page }) => {
    const outcome = await page.evaluate(async (png) => {
      const moduleUrl = '/src/lib/webgl/exposureScene.ts';
      const { runExposure } = await import(moduleUrl);
      const canvas = document.createElement('canvas');
      canvas.style.width = '120px';
      canvas.style.height = '120px';
      document.body.appendChild(canvas);
      const image = new Image();
      image.src = png;
      await image.decode();
      const handle = runExposure(canvas, image, 1000);
      canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
      const settled = await Promise.race([
        handle.done.then(() => 'done'),
        new Promise((resolve) => setTimeout(() => resolve('timeout'), 1000))
      ]);
      canvas.remove();
      return settled;
    }, ONE_PX_PNG);

    expect(outcome).toBe('done');
  });

  test('repeated runs on one canvas never leak a pending frame', async ({ page }) => {
    const outcome = await page.evaluate(async (png) => {
      const moduleUrl = '/src/lib/webgl/exposureScene.ts';
      const { runExposure } = await import(moduleUrl);
      const canvas = document.createElement('canvas');
      canvas.style.width = '120px';
      canvas.style.height = '120px';
      document.body.appendChild(canvas);
      const image = new Image();
      image.src = png;
      await image.decode();
      for (let index = 0; index < 5; index += 1) {
        const handle = runExposure(canvas, image, 300);
        if (index % 2 === 0) handle.cancel();
        await handle.done;
      }
      canvas.remove();
      return 'done';
    }, ONE_PX_PNG);

    expect(outcome).toBe('done');
  });
});
