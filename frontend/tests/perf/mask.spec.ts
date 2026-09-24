import { expect, test, type Page } from '@playwright/test';
import { disableRegionProcessing, loadApp } from '../e2e/fixtures/mockApi';
import { solidPng } from '../e2e/fixtures/png';

/**
 * Mask-editor performance tripwires.
 *
 * These are the measurements the v1.6 plan's acceptance criteria name: a long
 * diagonal drag on a 4096² mask must not drop frames the way reading the whole
 * stroke's bounding box on every pointermove used to (56 ms/frame measured on
 * this machine, versus 0.5 ms for one segment box).
 *
 * Run with: RUN_PERFORMANCE_TESTS=true npm --prefix frontend run test:perf:production
 */

const LONG_TASK_BUDGET_MS = 50;
const LARGE_PNG = solidPng(4096, 4096);

/**
 * Collect every long task the page reports while the callback runs.
 *
 * `{ buffered: true }` replays every long-task entry recorded since
 * navigation started, not just ones from `run()` — harmless for a test that
 * calls this as its first interaction with the page, but a trap for one that
 * calls it after other work (e.g. a full-frame smoothing repaint from
 * turning a radius on): that earlier task would get backdated into this
 * measurement. Recording `performance.now()` at observer-install time and
 * filtering by it keeps this measuring only what happens inside `run()`.
 */
async function withLongTaskWatch<T>(page: Page, run: () => Promise<T>) {
  await page.evaluate(() => {
    const state = { tasks: [] as number[], since: performance.now() };
    (window as unknown as { __longTasks: typeof state }).__longTasks = state;
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.startTime >= state.since) state.tasks.push(entry.duration);
        }
      }).observe({ type: 'longtask', buffered: true });
    } catch {
      // Engines without the longtask entry type simply report nothing.
    }
  });
  const result = await run();
  const durations = await page.evaluate(
    () => (window as unknown as { __longTasks: { tasks: number[] } }).__longTasks.tasks
  );
  return { result, durations };
}

test.describe('mask editor performance', () => {
  test.beforeEach(() => {
    test.skip(
      process.env.RUN_PERFORMANCE_TESTS !== 'true',
      'set RUN_PERFORMANCE_TESTS=true to run performance baselines'
    );
  });

  test('a long diagonal stroke paints without long tasks', async ({ page }, testInfo) => {
    const size = 4096;
    await loadApp(page);
    await page
      .getByLabel('Upload edit image')
      .setInputFiles([{ name: 'perf.png', mimeType: 'image/png', buffer: LARGE_PNG }]);
    await expect(page.getByRole('button', { name: 'Preview perf.png' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit repaint area for perf.png' }).click();
    const dialog = page.getByRole('dialog', { name: /Repaint area/ });
    await expect(dialog).toBeVisible();
    await page.getByRole('button', { name: '100% (original pixels)' }).click();

    const box = await dialog.locator('img').boundingBox();
    if (!box) throw new Error('the mask image has no layout box');
    const stage = await page.locator('canvas[aria-label="Mask canvas"]').boundingBox();
    if (!stage) throw new Error('the mask canvas has no layout box');

    // 200 pointer events across the viewport, i.e. a very long stroke through
    // the mask's coordinate space.
    const { result, durations } = await withLongTaskWatch(page, async () => {
      const started = Date.now();
      await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.45);
      await page.mouse.down();
      for (let step = 1; step <= 200; step += 1) {
        const t = step / 200;
        await page.mouse.move(
          box.x + box.width * (0.45 + 0.1 * t) + (stage.width * 0.6 * t),
          box.y + box.height * (0.45 + 0.1 * t) + (stage.height * 0.6 * t)
        );
      }
      await page.mouse.up();
      return Date.now() - started;
    });

    await testInfo.attach('mask-stroke-long-tasks.json', {
      body: JSON.stringify({ strokeMs: result, durations }, null, 2),
      contentType: 'application/json'
    });

    expect(durations.filter((duration) => duration > LONG_TASK_BUDGET_MS)).toEqual([]);
  });

  test('undo after painting with smoothing stays under the long-task budget', async ({ page }) => {
    // Plan Phase 4 / P4 acceptance: undoing a stroke command has to be served
    // by its patch (a local pixel restore + an O(1) tracker adjustment)
    // instead of the old full-canvas snapshot-restore-and-replay, which used
    // to cost ~500 ms at this size with smoothing on. Gap filling is switched
    // off so the measurement isolates undo/smoothing, not the auto-fill
    // worker pass — with it on, "Edit area" can sit on a pending "…" readout
    // well past a short wait while 20 scattered strokes work through the
    // debounced worker queue, which is a real but unrelated cost.
    const size = 4096;
    await disableRegionProcessing(page);
    await loadApp(page);
    await page
      .getByLabel('Upload edit image')
      .setInputFiles([{ name: 'perf-undo.png', mimeType: 'image/png', buffer: LARGE_PNG }]);
    await expect(page.getByRole('button', { name: 'Preview perf-undo.png' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit repaint area for perf-undo.png' }).click();
    const dialog = page.getByRole('dialog', { name: /Repaint area/ });
    await expect(dialog).toBeVisible();
    await page.getByRole('button', { name: '100% (original pixels)' }).click();

    await page.getByRole('button', { name: 'Region processing' }).click();
    const smoothing = page
      .locator('label')
      .filter({ hasText: 'Edge smoothing' })
      .locator('input[type="range"]');
    await smoothing.evaluate((element, value) => {
      const input = element as HTMLInputElement;
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, 16);
    await page.getByRole('button', { name: 'Region processing' }).click();

    // At 100% zoom a 4096² image is far bigger than the dialog, so its own
    // bounding box is mostly off-screen (the image is panned to center it);
    // scattering strokes across fractions of *that* box can walk right off
    // the visible panel and onto the backdrop button, closing the dialog.
    // Scatter within the stage's own (on-screen) box instead.
    const stage = await page.locator('canvas[aria-label="Mask canvas"]').boundingBox();
    if (!stage) throw new Error('the mask canvas has no layout box');

    // 20 short, separate strokes scattered across the canvas, each committed
    // (pointer up) so they land as 20 distinct undo-patch-eligible commands.
    for (let index = 0; index < 20; index += 1) {
      const x = stage.x + stage.width * (0.15 + 0.7 * (index / 20));
      const y = stage.y + stage.height * (0.15 + 0.7 * ((index % 5) / 5));
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 20, y + 20, { steps: 4 });
      await page.mouse.up();
    }
    // Let the debounced smoothing sync (150ms after the last stroke) settle
    // before measuring undo, so its work is not attributed to it.
    await page.waitForTimeout(400);

    const { durations } = await withLongTaskWatch(page, async () => {
      await page.keyboard.press('Control+z');
      await expect(page.getByText(/^Edit area/)).toBeVisible();
    });

    expect(durations.filter((duration) => duration > LONG_TASK_BUDGET_MS)).toEqual([]);
  });

  test('the coverage readout keeps up with the pointer', async ({ page }) => {
    await loadApp(page);
    await page
      .getByLabel('Upload edit image')
      .setInputFiles([{ name: 'perf-2.png', mimeType: 'image/png', buffer: LARGE_PNG }]);
    await expect(page.getByRole('button', { name: 'Preview perf-2.png' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit repaint area for perf-2.png' }).click();
    await expect(page.getByRole('dialog', { name: /Repaint area/ })).toBeVisible();

    const box = await page.getByRole('dialog', { name: /Repaint area/ }).locator('img').boundingBox();
    if (!box) throw new Error('the mask image has no layout box');
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.7, { steps: 40 });

    // Still holding the pointer down: the readout has to have moved already.
    const readout = page.getByText(/^Edit area/);
    await expect(readout).not.toHaveText('Edit area 0.0%');
    await page.mouse.up();
  });
});
