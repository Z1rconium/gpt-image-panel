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
    if (typeof PerformanceObserver === 'undefined' ||
        !PerformanceObserver.supportedEntryTypes.includes('longtask')) {
      throw new Error('This browser does not expose longtask entries; the performance baseline cannot be measured');
    }
    const state = { tasks: [] as number[], since: performance.now() };
    (window as unknown as { __longTasks: typeof state }).__longTasks = state;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.startTime >= state.since) state.tasks.push(entry.duration);
      }
    }).observe({ type: 'longtask', buffered: true });
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

  test('4K auto-fill settles within one second after a rectangle', async ({ page }, testInfo) => {
    await loadApp(page);
    await page.getByLabel('Upload edit image').setInputFiles([
      { name: 'perf-fill.png', mimeType: 'image/png', buffer: LARGE_PNG }
    ]);
    await page.getByRole('button', { name: 'Edit repaint area for perf-fill.png' }).click();
    await expect(page.getByRole('dialog', { name: /Repaint area/ })).toBeVisible();
    await page.getByRole('button', { name: 'Rectangle' }).click();
    const box = await page.getByRole('dialog', { name: /Repaint area/ }).locator('img').boundingBox();
    if (!box) throw new Error('the mask image has no layout box');
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.8, { steps: 6 });
    await page.evaluate(() => { (window as unknown as { __fillStart: number }).__fillStart = performance.now(); });
    await page.mouse.up();
    const readout = page.getByText(/^Edit area/);
    await expect(readout).toContainText('…');
    await expect(readout).not.toContainText('…', { timeout: 5_000 });
    const elapsedMs = await page.evaluate(() => performance.now() - (window as unknown as { __fillStart: number }).__fillStart);
    await testInfo.attach('mask-auto-fill-timing.json', {
      body: JSON.stringify({ imageSize: 4096, elapsedMs }, null, 2),
      contentType: 'application/json'
    });
    expect(elapsedMs, `4K auto-fill took ${elapsedMs.toFixed(0)} ms`).toBeLessThanOrEqual(1_000);
  });

  test('4K auto-fill closes a real seam within one second', async ({ page }, testInfo) => {
    await loadApp(page);
    await page.getByLabel('Upload edit image').setInputFiles([
      { name: 'perf-seam.png', mimeType: 'image/png', buffer: LARGE_PNG }
    ]);
    await page.getByRole('button', { name: 'Edit repaint area for perf-seam.png' }).click();
    const dialog = page.getByRole('dialog', { name: /Repaint area/ });
    await expect(dialog).toBeVisible();
    await page.getByRole('button', { name: '100% (original pixels)' }).click();
    const slider = page.locator('label').filter({ hasText: 'Brush' }).locator('input[type="range"]');
    await slider.evaluate((element) => {
      const input = element as HTMLInputElement;
      input.value = '8';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });

    const stage = await page.locator('canvas[aria-label="Mask canvas"]').boundingBox();
    const image = await dialog.locator('img').boundingBox();
    if (!stage || !image) throw new Error('the mask image has no layout box');
    const centerX = stage.x + stage.width / 2;
    const centerY = stage.y + stage.height / 2;
    const stroke = async (x: number) => {
      await page.mouse.move(x, centerY - 120);
      await page.mouse.down();
      await page.mouse.move(x, centerY + 120, { steps: 4 });
      await page.mouse.up();
    };
    await stroke(centerX - 8);
    const readout = page.getByText(/^Edit area/);
    await expect(readout).not.toContainText('…', { timeout: 5_000 });

    await page.mouse.move(centerX + 8, centerY - 120);
    await page.mouse.down();
    await page.mouse.move(centerX + 8, centerY + 120, { steps: 4 });
    await page.evaluate(() => { (window as unknown as { __seamStart: number }).__seamStart = performance.now(); });
    await page.mouse.up();
    await expect(readout).toContainText('…');
    await expect(readout).not.toContainText('…', { timeout: 5_000 });
    const elapsedMs = await page.evaluate(() => performance.now() - (window as unknown as { __seamStart: number }).__seamStart);
    await testInfo.attach('mask-seam-fill-timing.json', {
      body: JSON.stringify({ imageSize: 4096, elapsedMs }, null, 2),
      contentType: 'application/json'
    });
    expect(elapsedMs, `4K seam fill took ${elapsedMs.toFixed(0)} ms`).toBeLessThanOrEqual(1_000);

    // Verify the measured pass filled pixels between the two strokes. The
    // export happens after timing, so PNG encoding is outside the fill budget.
    await page.getByRole('button', { name: 'Save selection' }).click();
    await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('seam');
    const requestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
    await page.getByRole('button', { name: 'Edits' }).click();
    const body = (await requestPromise).postDataBuffer();
    const headerIndex = body?.indexOf(Buffer.from('name="mask"', 'latin1')) ?? -1;
    if (!body || headerIndex < 0) throw new Error('the edit request carried no mask');
    const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const start = body.indexOf(signature, headerIndex);
    const end = body.indexOf(Buffer.from('\r\n--'), start);
    if (start < 0 || end < 0) throw new Error('the edit request carried no PNG mask');
    const x = Math.round((centerX - image.x) * 4096 / image.width);
    const y = Math.round((centerY - image.y) * 4096 / image.height);
    const alpha = await page.evaluate(async ({ base64, x, y }) => {
      const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('no 2d context');
      context.drawImage(bitmap, 0, 0);
      return context.getImageData(x, y, 1, 1).data[3];
    }, { base64: body.subarray(start, end).toString('base64'), x, y });
    expect(alpha, 'the exported seam pixel should be editable').toBe(0);
  });

  test('undo after painting with smoothing stays under the long-task budget', async ({ page }, testInfo) => {
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

    await page.evaluate(() => {
      (window as unknown as { __undoMs: number | null }).__undoMs = null;
      window.addEventListener('keydown', () => {
        const started = performance.now();
        setTimeout(() => {
          (window as unknown as { __undoMs: number | null }).__undoMs = performance.now() - started;
        }, 0);
      }, { capture: true, once: true });
    });

    const { durations } = await withLongTaskWatch(page, async () => {
      await page.keyboard.press('Control+z');
      await expect(page.getByText(/^Edit area/)).toBeVisible();
    });

    await expect.poll(() => page.evaluate(() => (window as unknown as { __undoMs: number | null }).__undoMs)).not.toBeNull();
    const undoMs = await page.evaluate(() => (window as unknown as { __undoMs: number }).__undoMs);
    await testInfo.attach('mask-undo-timing.json', {
      body: JSON.stringify({ imageSize: 4096, strokes: 20, smoothingPx: 16, undoMs, durations }, null, 2),
      contentType: 'application/json'
    });

    expect(durations.filter((duration) => duration > LONG_TASK_BUDGET_MS)).toEqual([]);
    expect(undoMs).toBeLessThanOrEqual(50);
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
