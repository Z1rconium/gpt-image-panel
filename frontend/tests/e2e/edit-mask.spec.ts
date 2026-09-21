import { expect, test, type Page } from '@playwright/test';
import { job, loadApp } from './fixtures/mockApi';

// 64x64 source and matching 64x64 mask (transparent 32x32 center), plus a
// 32x32 mask that intentionally mismatches the primary image.
const SOURCE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAYklEQVR4nO3PMQ0AIADAMEAN/lUgCxEcDcmqYJtn7/GzpQNeNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaBdCH8BmPRLpIsAAAAASUVORK5CYII=',
  'base64'
);
const MASK_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAqUlEQVR4nO3bwQnAMAzAQKd0/5XbIRK4R6QBjBB+Gbxm5puLebSApgBaQFMALaApgBbQFEALaAqgBTQF0AKaAmgBzXtgxjowY4ete8b1G1AALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0BdACmgJoAU0BtIBm9S9wOQXQApoCaAFNAbSApgBaQFMALaApgBbQFEALaH4NOAN/t9r+BwAAAABJRU5ErkJggg==',
  'base64'
);
const WRONG_SIZE_MASK_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAGklEQVR4nO3BAQEAAACCIP+vbkhAAQAAAO8GECAAARlDNO4AAAAASUVORK5CYII=',
  'base64'
);

const MASK_CANVAS = 'canvas[aria-label="Mask canvas"]';
const MASK_INPUT = 'input[type="file"][accept="image/png"]';

async function uploadPrimary(page: Page, name = 'mask-source.png') {
  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name, mimeType: 'image/png', buffer: SOURCE_PNG }]);
  await expect(page.getByRole('button', { name: `Preview ${name}` })).toBeVisible();
}

async function openMaskEditor(page: Page, label = 'mask-source.png') {
  await page.getByRole('button', { name: `Edit repaint area for ${label}` }).click();
  await expect(page.getByRole('dialog', { name: /Repaint area/ })).toBeVisible();
  await expect(page.locator(MASK_CANVAS)).toBeVisible();
}

async function paintStroke(page: Page) {
  const canvas = page.locator(MASK_CANVAS);
  const box = await canvas.boundingBox();
  if (!box) throw new Error('mask canvas has no layout box');
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.5, { steps: 8 });
  await page.mouse.up();
}

test('painting a mask applies it to the primary card', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeDisabled();

  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByRole('dialog', { name: /Repaint area/ })).toHaveCount(0);
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Selection saved');
});

test('adding another reference image keeps the applied mask', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();

  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name: 'second.png', mimeType: 'image/png', buffer: SOURCE_PNG }]);

  await expect(page.getByRole('button', { name: 'Preview second.png' })).toBeVisible();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
});

test('removing the primary image discards the mask and warns', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();

  await page.getByRole('button', { name: 'Remove mask-source.png' }).click();

  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Mask cleared because the primary image changed');
});

function pngFromMultipart(body: Buffer, fieldName: string): Buffer | null {
  const headerIndex = body.indexOf(Buffer.from(`name="${fieldName}"`, 'latin1'));
  if (headerIndex < 0) return null;
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const start = body.indexOf(signature, headerIndex);
  if (start < 0) return null;
  const end = body.indexOf(Buffer.from('\r\n--'), start);
  return end < 0 ? null : body.subarray(start, end);
}

test('submitting an edit attaches the applied mask as mask.png', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('masked edit prompt');
  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const requestBody = (await editRequestPromise).postDataBuffer();
  const body = requestBody?.toString('latin1') || '';

  expect(body).toContain('name="mask"');
  expect(body).toContain('filename="mask.png"');
  expect(body).toContain('filename="mask-source.png"');

  // Decode the uploaded mask: the painted region must be fully transparent
  // (the upstream edits alpha==0 pixels) and the rest must stay opaque black.
  const maskPng = requestBody ? pngFromMultipart(requestBody, 'mask') : null;
  expect(maskPng).not.toBeNull();
  const layout = await page.evaluate(async (base64: string) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('no 2d context');
    context.drawImage(bitmap, 0, 0);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const alphaAt = (x: number, y: number) => data[(y * canvas.width + x) * 4 + 3];
    return {
      width: bitmap.width,
      height: bitmap.height,
      painted: alphaAt(32, 32),
      kept: alphaAt(2, 2)
    };
  }, maskPng!.toString('base64'));

  expect(layout.width).toBe(64);
  expect(layout.height).toBe(64);
  expect(layout.painted).toBe(0);
  expect(layout.kept).toBe(255);
});

test('uploading a valid mask PNG imports its transparent area', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page
    .locator(MASK_INPUT)
    .setInputFiles([{ name: 'mask.png', mimeType: 'image/png', buffer: MASK_PNG }]);

  await expect(page.getByText('Edit area 25.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeEnabled();
});

async function canvasPixel(page: Page, x: number, y: number) {
  return page.evaluate(
    ({ px, py }) => {
      const canvas = document.querySelector('canvas[aria-label="Mask canvas"]') as HTMLCanvasElement | null;
      if (!canvas) throw new Error('mask canvas missing');
      const context = canvas.getContext('2d');
      if (!context) throw new Error('mask canvas has no 2d context');
      return Array.from(context.getImageData(px, py, 1, 1).data);
    },
    { px: x, py: y }
  );
}

test('switching between overlay and mask-only re-renders the canvas', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);

  // Overlay leaves the photo visible outside the marked region.
  expect((await canvasPixel(page, 3, 3))[3]).toBe(0);
  const overlayMark = await canvasPixel(page, 32, 32);
  expect(overlayMark[3]).toBeGreaterThan(0);
  expect(overlayMark[1]).toBeGreaterThan(overlayMark[0]);

  await page.getByRole('button', { name: 'Mask only' }).click();
  await expect.poll(async () => (await canvasPixel(page, 3, 3))[3]).toBeGreaterThan(0);
  const maskOnlyMark = await canvasPixel(page, 32, 32);
  expect(maskOnlyMark[3]).toBeGreaterThan(0);
  expect(maskOnlyMark[1]).toBeGreaterThan(maskOnlyMark[0]);

  // The unmarked area must read as a transparency checkerboard, not a flat fill.
  const dpr = await page.evaluate(() => window.devicePixelRatio || 1);
  const cell = 8 * Math.max(1, Math.round(dpr));
  const lightSquare = await canvasPixel(page, Math.round(cell / 2), Math.round(cell / 2));
  const darkSquare = await canvasPixel(page, Math.round(cell * 1.5), Math.round(cell / 2));
  expect(lightSquare).not.toEqual(darkSquare);

  await page.getByRole('button', { name: 'Overlay' }).click();
  await expect.poll(async () => (await canvasPixel(page, 3, 3))[3]).toBe(0);
});

test('hides the mask entry when the active preset does not support masks', async ({ page }) => {
  await loadApp(page, { maskSupported: false });
  await uploadPrimary(page);

  await expect(page.getByText('Primary')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit repaint area for mask-source.png' })).toHaveCount(0);
});

test('disabling mask support on the active preset clears the applied mask', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByLabel('Supports mask inpainting').uncheck();
  const saveRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST'
  );
  await drawer.getByRole('button', { name: 'Save Preset' }).click();
  const body = (await saveRequest).postDataJSON() as Record<string, unknown>;
  expect(body.supports_mask).toBe(false);

  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('does not support masks');
  await expect(page.getByRole('button', { name: 'Edit repaint area for mask-source.png' })).toHaveCount(0);
});

test('rejects an uploaded mask whose size differs from the primary image', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page
    .locator(MASK_INPUT)
    .setInputFiles([{ name: 'mask-32.png', mimeType: 'image/png', buffer: WRONG_SIZE_MASK_PNG }]);

  await expect(page.getByText('The mask is 32x32 but the primary image is 64x64')).toBeVisible();
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
});

test('escape closes the editor and undo clears the painted coverage', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);

  await openMaskEditor(page);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: /Repaint area/ })).toHaveCount(0);

  await openMaskEditor(page);
  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.keyboard.press('Control+z');
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeDisabled();
});

test('mask editor stays usable at a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 });
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await expect(page.getByRole('button', { name: 'Brush' })).toBeVisible();
  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
});

async function canvasStrokePaths(
  page: Page,
  paths: { x: number; y: number }[][]
) {
  const box = await page.locator(MASK_CANVAS).boundingBox();
  if (!box) throw new Error('mask canvas has no layout box');
  const start = paths[0][0];
  await page.mouse.move(box.x + box.width * start.x, box.y + box.height * start.y);
  await page.mouse.down();
  for (const point of paths[0].slice(1)) {
    await page.mouse.move(box.x + box.width * point.x, box.y + box.height * point.y, { steps: 6 });
  }
  await page.mouse.up();
}

test('the rectangle tool fills a dragged region', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Rectangle' }).click();
  await canvasStrokePaths(page, [
    [
      { x: 0.25, y: 0.25 },
      { x: 0.75, y: 0.75 }
    ]
  ]);

  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
});

test('the lasso tool fills a closed region', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Lasso' }).click();
  await canvasStrokePaths(page, [
    [
      { x: 0.3, y: 0.2 },
      { x: 0.7, y: 0.3 },
      { x: 0.5, y: 0.8 },
      { x: 0.3, y: 0.2 }
    ]
  ]);

  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
});

test('painting still lands on the image while zoomed in', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Zoom in' }).click();
  await page.getByRole('button', { name: 'Zoom in' }).click();
  await expect(page.getByText('156%')).toBeVisible();

  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.getByRole('button', { name: 'Fit canvas' }).click();
  await expect(page.locator('span').filter({ hasText: /^100%$/ })).toBeVisible();
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
});

test('uploading a mask over an existing selection offers replace or merge', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  // Replace discards the painted stroke: only the imported center square stays.
  await page
    .locator(MASK_INPUT)
    .setInputFiles([{ name: 'mask.png', mimeType: 'image/png', buffer: MASK_PNG }]);
  await page.getByRole('button', { name: 'Replace selection' }).click();
  await expect(page.getByText('Edit area 25.0%')).toBeVisible();

  // Undo restores the painted stroke instead of the imported mask.
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  // Merge keeps both regions: the painted stroke plus the imported square.
  await page
    .locator(MASK_INPUT)
    .setInputFiles([{ name: 'mask.png', mimeType: 'image/png', buffer: MASK_PNG }]);
  await page.getByRole('button', { name: 'Merge with existing' }).click();
  const coverageText = await page.getByText(/Edit area \d+\.\d%/).textContent();
  expect(parseFloat(coverageText!.replace(/[^\d.]/g, ''))).toBeGreaterThan(25);
});

test('clearing an existing mask can be saved as no mask', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();

  await openMaskEditor(page);
  const editorDialog = page.getByRole('dialog', { name: /Repaint area/ });
  await editorDialog.getByRole('button', { name: 'Clear', exact: true }).click();
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toHaveCount(0);
  await editorDialog.getByRole('button', { name: 'Save without mask' }).click();

  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Saved as no mask');
});

test('the rectangle tool supports visible add and subtract modes', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Rectangle' }).click();
  await expect(page.getByRole('button', { name: 'Add', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Subtract' })).toBeVisible();

  await canvasStrokePaths(page, [
    [
      { x: 0.2, y: 0.2 },
      { x: 0.8, y: 0.8 }
    ]
  ]);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.getByRole('button', { name: 'Subtract' }).click();
  await canvasStrokePaths(page, [
    [
      { x: 0.2, y: 0.2 },
      { x: 0.5, y: 0.5 }
    ]
  ]);

  const coverageText = await page.getByText(/Edit area \d+\.\d%/).textContent();
  expect(parseFloat(coverageText!.replace(/[^\d.]/g, ''))).toBeLessThan(36);
});

test('the move view tool pans without painting', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Move view' }).click();
  const canvas = page.locator(MASK_CANVAS);
  const box = await canvas.boundingBox();
  if (!box) throw new Error('mask canvas has no layout box');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 - 40, box.y + box.height / 2 - 20, { steps: 5 });
  await page.mouse.up();

  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeDisabled();
});

test('retrying a masked edit job restores the original task mask', async ({ page }) => {
  await loadApp(page, {
    historyJobs: [{ ...job('history-mask', 'masked retry prompt'), operation: 'edit', mask_applied: true }]
  });
  await uploadPrimary(page);

  await page.getByRole('button', { name: 'Job History' }).click();
  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await jobsDrawer.getByRole('button', { name: 'History', exact: true }).click();
  const historyJob = jobsDrawer.locator('article').filter({ hasText: 'masked retry prompt' });
  await historyJob.getByRole('button', { name: 'Retry' }).click();

  // The original task's own mask is restored onto the primary source, and the
  // retry submits with it attached instead of reusing a current selection.
  await expect(page.getByText('Repaint 25.0%')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Original task selection restored');

  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const body = (await editRequestPromise).postDataBuffer()?.toString('latin1') || '';
  expect(body).toContain('name="mask"');
  expect(body).toContain('filename="mask.png"');
});

test('retrying an edit job that used no mask ignores the current selection', async ({ page }) => {
  await loadApp(page, {
    historyJobs: [{ ...job('history-plain', 'plain retry prompt'), operation: 'edit', mask_applied: false }]
  });
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();

  await page.getByRole('button', { name: 'Job History' }).click();
  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await jobsDrawer.getByRole('button', { name: 'History', exact: true }).click();
  const historyJob = jobsDrawer.locator('article').filter({ hasText: 'plain retry prompt' });
  await historyJob.getByRole('button', { name: 'Retry' }).click();

  // The original task had no mask, so the freshly painted selection is dropped.
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'This job used no mask' })).toBeVisible();

  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const body = (await editRequestPromise).postDataBuffer()?.toString('latin1') || '';
  expect(body).not.toContain('name="mask"');
});
