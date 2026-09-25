import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { baseGalleryImages, disableRegionProcessing, job, loadApp } from './fixtures/mockApi';
import { blackWhiteMaskPng, blockPng, opaqueRgbaBlackWhiteMaskPng, softAlphaMaskPng, solidPng, twoTonePng } from './fixtures/png';

// 64x64 source and matching 64x64 mask (transparent 32x32 center).
const SOURCE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAYklEQVR4nO3PMQ0AIADAMEAN/lUgCxEcDcmqYJtn7/GzpQNeNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaBdCH8BmPRLpIsAAAAASUVORK5CYII=',
  'base64'
);
const MASK_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAqUlEQVR4nO3bwQnAMAzAQKd0/5XbIRK4R6QBjBB+Gbxm5puLebSApgBaQFMALaApgBbQFEALaAqgBTQF0AKaAmgBzXtgxjowY4ete8b1G1AALaApgBbQFEALaAqgBTQF0AKaAmgBTQG0gKYAWkBTAC2gKYAW0BRAC2gKoAU0BdACmgJoAU0BtIBm9S9wOQXQApoCaAFNAbSApgBaQFMALaApgBbQFEALaH4NOAN/t9r+BwAAAABJRU5ErkJggg==',
  'base64'
);

const HUGE_PNG = solidPng(4096, 4096);
const TWO_TONE_PNG = twoTonePng(64, 64);

const LARGE_PNG = solidPng(2048, 2048);
const SMALL_PNG = solidPng(256, 256);
const MEDIUM_PNG = solidPng(512, 512);
const EDGE_PNG = blockPng(200, 200, { x: 70, y: 70, size: 60 });

const MASK_CANVAS = 'canvas[aria-label="Mask canvas"]';
const MASK_INPUT = 'input[type="file"][aria-label="Upload mask"]';
const EDITOR_DIALOG = (page: Page) => page.getByRole('dialog', { name: /Repaint area/ });

async function uploadPrimary(page: Page, name = 'mask-source.png') {
  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name, mimeType: 'image/png', buffer: SOURCE_PNG }]);
  await expect(page.getByRole('button', { name: `Preview ${name}` })).toBeVisible();
}

async function openMaskEditor(page: Page, label = 'mask-source.png') {
  await page.getByRole('button', { name: `Edit repaint area for ${label}` }).click();
  await expect(EDITOR_DIALOG(page)).toBeVisible();
  await expect(page.locator(MASK_CANVAS)).toBeVisible();
}

// The overlay canvas is the whole stage viewport now; strokes must be aimed
// at the image rectangle inside it (fit-centered by the editor).
async function maskImageBox(page: Page) {
  const box = await EDITOR_DIALOG(page)
    .locator('img')
    .boundingBox();
  if (!box) throw new Error('mask image has no layout box');
  return box;
}

async function paintStroke(page: Page) {
  const box = await maskImageBox(page);
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
  expect(body).toContain('name="paste_back"');
  expect(body).toContain('true');
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

// The fit-centered image shares its center with the stage-sized canvas, and
// the default stroke crosses that center, so the center pixel must be marked.
async function canvasCenterPixel(page: Page) {
  return page.evaluate(() => {
    const canvas = document.querySelector('canvas[aria-label="Mask canvas"]') as HTMLCanvasElement | null;
    if (!canvas) throw new Error('mask canvas missing');
    const context = canvas.getContext('2d');
    if (!context) throw new Error('mask canvas has no 2d context');
    return Array.from(
      context.getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data
    );
  });
}

test('switching between overlay and mask-only re-renders the canvas', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);

  // Overlay leaves the photo visible outside the marked region.
  expect((await canvasPixel(page, 3, 3))[3]).toBe(0);
  const overlayMark = await canvasCenterPixel(page);
  expect(overlayMark[3]).toBeGreaterThan(0);
  expect(overlayMark[1]).toBeGreaterThan(overlayMark[0]);

  await page.getByRole('button', { name: 'Mask only' }).click();
  await expect.poll(async () => (await canvasPixel(page, 3, 3))[3]).toBeGreaterThan(0);
  const maskOnlyMark = await canvasCenterPixel(page);
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

test('the overlay canvas is sized to the stage viewport, not the image', async ({ page }) => {
  await loadApp(page);
  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name: 'huge.png', mimeType: 'image/png', buffer: HUGE_PNG }]);
  await expect(page.getByRole('button', { name: 'Preview huge.png' })).toBeVisible();
  await openMaskEditor(page, 'huge.png');

  const metrics = await page.evaluate(() => {
    const canvas = document.querySelector('canvas[aria-label="Mask canvas"]') as HTMLCanvasElement | null;
    if (!canvas) throw new Error('mask canvas missing');
    const stage = canvas.parentElement;
    if (!stage) throw new Error('mask canvas has no stage parent');
    const rect = stage.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    return {
      canvasWidth: canvas.width,
      canvasHeight: canvas.height,
      stageWidth: rect.width,
      stageHeight: rect.height,
      dpr
    };
  });

  expect(metrics.canvasWidth).toBeGreaterThan(0);
  expect(metrics.canvasHeight).toBeGreaterThan(0);
  expect(metrics.canvasWidth).toBeLessThanOrEqual(Math.round(metrics.stageWidth * metrics.dpr) + 1);
  expect(metrics.canvasHeight).toBeLessThanOrEqual(Math.round(metrics.stageHeight * metrics.dpr) + 1);
  // The pre-viewport implementation allocated the canvas at image resolution.
  expect(metrics.canvasWidth).toBeLessThan(4096);
  expect(metrics.canvasHeight).toBeLessThan(4096);
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

test('offers nearest-neighbor scaling for a 2× same-ratio uploaded mask', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page
    .locator(MASK_INPUT)
    .setInputFiles([{ name: 'mask-128.png', mimeType: 'image/png', buffer: softAlphaMaskPng(128, 128) }]);

  await expect(page.getByText(/Scale with nearest neighbor and import/)).toBeVisible();
  await page.getByRole('button', { name: 'Scale and import' }).click();
  await expect(page.getByText('Edit area 50.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeEnabled();
});

test('imports soft alpha below 128 and black-white masks', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await page.locator(MASK_INPUT).setInputFiles([{ name: 'soft.png', mimeType: 'image/png', buffer: softAlphaMaskPng(64, 64) }]);
  await expect(page.getByText('Edit area 50.0%')).toBeVisible();
  await page.locator(MASK_INPUT).setInputFiles([{ name: 'bw.png', mimeType: 'image/png', buffer: blackWhiteMaskPng(64, 64) }]);
  await page.getByRole('button', { name: 'Replace selection' }).click();
  await expect(page.getByText('Edit area 25.0%')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('white is editable');
  await page.locator(MASK_INPUT).setInputFiles([{ name: 'bw-rgba.png', mimeType: 'image/png', buffer: opaqueRgbaBlackWhiteMaskPng(64, 64) }]);
  await page.getByRole('button', { name: 'Replace selection' }).click();
  await expect(page.getByText('Edit area 25.0%')).toBeVisible();
});

test('paste-back switch is submitted with a masked edit', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await page.getByRole('checkbox', { name: 'Paste back' }).uncheck();
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('masked edit prompt');
  const requestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  expect((await requestPromise).postData()).toContain('false');
});

test('preview compares the masked result with its submitted upload after the source is cleared', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('compare masked edit');
  await page.getByRole('button', { name: 'Edits' }).click();

  const compare = page.getByRole('button', { name: 'Compare with original' });
  await expect(compare).toBeVisible();
  await expect(compare).toHaveAttribute('aria-pressed', 'false');
  await compare.click();
  const original = page.getByRole('img', { name: 'Original source image: mask-source.png' });
  await expect(original).toBeVisible();
  await expect(original).toHaveAttribute('src', /^blob:/);
  await expect(page.locator('img[alt="Generated preview"]')).toHaveAttribute('aria-hidden', 'true');

  await page.getByRole('button', { name: 'Remove mask-source.png' }).click();
  await expect(original).toBeVisible();
  await page.getByRole('button', { name: 'Show edited result' }).click();
  await expect(original).toHaveCount(0);
  await expect(compare).toHaveAttribute('aria-pressed', 'false');

  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(compare).toHaveCount(0);
});

test('preview compares a masked gallery edit with its gallery source', async ({ page }) => {
  await loadApp(page, {
    galleryImages: [{ ...baseGalleryImages[0], image_width: 64, image_height: 64 }]
  });
  await page.route('**/api/image/img-1.png', (route) => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: SOURCE_PNG
  }));
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Keep original prompt', exact: true }).click();
  await openMaskEditor(page, 'Gallery: img-1.png');
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('compare gallery edit');
  await page.getByRole('button', { name: 'Edits' }).click();

  await page.getByRole('button', { name: 'Compare with original' }).click();
  const original = page.getByRole('img', { name: 'Original source image: Gallery: img-1.png' });
  await expect(original).toHaveAttribute('src', '/api/image/img-1.png');
  await page.getByRole('button', { name: 'Remove Gallery: img-1.png' }).click();
  await expect(original).toBeVisible();
});

test('job history shows applied and skipped paste-back reasons', async ({ page }) => {
  const applied = job('paste-applied', 'applied edit');
  const skipped = job('paste-skipped', 'skipped edit');
  await loadApp(page, {
    historyJobs: [
      { ...applied, operation: 'edit', mask_applied: true, images: [{ ...applied.images[0], paste_back: 'applied', paste_back_scale: 2 }] },
      { ...skipped, operation: 'edit', mask_applied: true, images: [{ ...skipped.images[0], paste_back: 'skipped:aspect_mismatch' }] }
    ]
  });
  await page.getByRole('button', { name: 'Job History' }).click();
  await page.getByRole('dialog', { name: 'Job History' }).getByRole('button', { name: 'History', exact: true }).click();
  await expect(page.getByText('Pasted back', { exact: true })).toBeVisible();
  await expect(page.getByText('Not pasted back', { exact: true })).toBeVisible();
  await expect(page.locator('[title*="Edit area enlarged ×2.0"]')).toBeVisible();
  await expect(page.locator('[title*="Aspect ratios differ"]')).toBeVisible();
});

test('coverage guidance flags very small and nearly full selections', async ({ page }) => {
  await disableRegionProcessing(page);
  await loadApp(page);
  await uploadSizedPrimary(page, LARGE_PNG, 'coverage-guidance.png');
  await setBrushSize(page, 4);
  await paintStroke(page);
  await expect(page.getByText('The edit area is very small. Was that intentional?')).toBeVisible();
  await page.getByRole('button', { name: 'Invert', exact: true }).click();
  await expect(page.getByText('Almost the whole image is selected. Consider editing without a mask.')).toBeVisible();
});

test('Shift-click draws from the previous brush endpoint', async ({ page }) => {
  await disableRegionProcessing(page);
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  const box = await maskImageBox(page);
  await page.mouse.click(box.x + box.width * 0.2, box.y + box.height * 0.5);
  await page.keyboard.down('Shift');
  await page.mouse.click(box.x + box.width * 0.8, box.y + box.height * 0.5);
  await page.keyboard.up('Shift');
  await expect.poll(async () => {
    const text = await page.getByText(/Edit area \d+\.\d%/).textContent();
    return parseFloat(text?.replace(/[^\d.]/g, '') || '0');
  }).toBeGreaterThan(3);
});

test('Shift-drag constrains rectangles and ellipses to square bounds', async ({ page }) => {
  await disableRegionProcessing(page);
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  const box = await maskImageBox(page);
  for (const tool of ['Rectangle', 'Ellipse']) {
    await page.getByRole('button', { name: tool }).click();
    await page.keyboard.down('Shift');
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.4, { steps: 5 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
    await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();
    await EDITOR_DIALOG(page).getByRole('button', { name: 'Clear', exact: true }).click();
  }
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

  // The region-processing panel has to fit the same viewport and stay usable:
  // its sliders are the only way to reach the smoothing and edge controls.
  await page.getByRole('button', { name: 'Region processing' }).click();
  const panel = page.getByRole('note', { name: 'Region processing' });
  await expect(panel).toBeVisible();
  const panelBox = await panel.boundingBox();
  const dialogBox = await EDITOR_DIALOG(page).boundingBox();
  expect(panelBox).not.toBeNull();
  expect(dialogBox).not.toBeNull();
  expect(panelBox!.x).toBeGreaterThanOrEqual(dialogBox!.x);
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(dialogBox!.x + dialogBox!.width + 1);
  await expect(panel.getByRole('slider', { name: /Edge smoothing/ })).toBeVisible();
  await page.getByRole('button', { name: 'Region processing' }).click();
  await expect(panel).toHaveCount(0);

  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
});

async function canvasStrokePaths(
  page: Page,
  paths: { x: number; y: number }[][]
) {
  const box = await maskImageBox(page);
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

  const restoreRequestPromise = page.waitForRequest((request) =>
    new URL(request.url()).pathname === '/api/generate/history-mask/mask/restore'
  );

  await page.getByRole('button', { name: 'Job History' }).click();
  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await jobsDrawer.getByRole('button', { name: 'History', exact: true }).click();
  const historyJob = jobsDrawer.locator('article').filter({ hasText: 'masked retry prompt' });
  await historyJob.getByRole('button', { name: 'Retry' }).click();
  const restoreRequest = await restoreRequestPromise;
  expect(restoreRequest.method()).toBe('POST');
  expect(restoreRequest.postData() || '').toContain('name="source_sha256"');
  expect(restoreRequest.postData() || '').toContain(createHash('sha256').update(SOURCE_PNG).digest('hex'));

  // The original task's own mask is restored onto the primary source, and the
  // retry submits with it attached instead of reusing a current selection.
  await expect(page.getByText('Repaint 25.0%')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Original task selection restored' })).toBeVisible();

  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const body = (await editRequestPromise).postDataBuffer()?.toString('latin1') || '';
  expect(body).toContain('name="mask"');
  expect(body).toContain('filename="mask.png"');
});

test('a mismatched or legacy masked job clears the selection and requires re-editing', async ({ page }) => {
  await loadApp(page, {
    maskRestoreStatus: 409,
    historyJobs: [{ ...job('history-mask', 'legacy masked prompt'), operation: 'edit', mask_applied: true }]
  });
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
  let editRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/edits') editRequests += 1;
  });

  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  await drawer.locator('article').filter({ hasText: 'legacy masked prompt' }).getByRole('button', { name: 'Retry' }).click();

  await expect(EDITOR_DIALOG(page)).toBeVisible();
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  expect(editRequests).toBe(0);
});

test('a missing saved mask never submits the current selection automatically', async ({ page }) => {
  await loadApp(page, {
    maskRestoreStatus: 404,
    historyJobs: [{ ...job('history-mask', 'missing mask prompt'), operation: 'edit', mask_applied: true }]
  });
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Save selection' }).click();
  let editRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/edits') editRequests += 1;
  });

  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  await drawer.locator('article').filter({ hasText: 'missing mask prompt' }).getByRole('button', { name: 'Retry' }).click();

  await expect(EDITOR_DIALOG(page)).toBeVisible();
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  expect(editRequests).toBe(0);
});

test('gallery retry sends the image ID for server-side file verification', async ({ page }) => {
  await loadApp(page, {
    galleryImages: [{ ...baseGalleryImages[0], image_width: 64, image_height: 64 }],
    historyJobs: [{ ...job('history-mask', 'gallery retry prompt'), operation: 'edit', mask_applied: true }]
  });
  await page.route('**/api/image/img-1.png', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: SOURCE_PNG }));
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Keep original prompt', exact: true }).click();

  const restoreRequestPromise = page.waitForRequest((request) =>
    new URL(request.url()).pathname === '/api/generate/history-mask/mask/restore'
  );
  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  await drawer.locator('article').filter({ hasText: 'gallery retry prompt' }).getByRole('button', { name: 'Retry' }).click();
  const request = await restoreRequestPromise;
  expect(request.postData() || '').toContain('name="gallery_image_id"');
  expect(request.postData() || '').toContain('img-1');
  expect(request.postData() || '').not.toContain('name="source_sha256"');
  await expect(page.getByRole('status').filter({ hasText: 'Original task selection restored' })).toBeVisible();
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

async function paintGrowingStroke(page: Page, endFraction: number) {
  const box = await maskImageBox(page);
  const y = box.y + box.height * 0.5;
  await page.mouse.move(box.x + box.width * 0.1, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * endFraction, y, { steps: 4 });
  await page.mouse.up();
}

function parseCoverage(text: string | null): number {
  return parseFloat((text ?? '').replace(/[^\d.]/g, ''));
}

test('undo across a checkpoint boundary restores the prior coverage exactly', async ({ page }) => {
  // maskDocument.ts snapshots an undo checkpoint every 8 committed commands
  // (CHECKPOINT_INTERVAL); ten strokes and ten matching undos exercise both
  // the patch-based undo path (plan Phase 4 / P4) and, once a stroke's patch
  // predates the retained checkpoints, the checkpoint-and-replay fallback.
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  const strokeCount = 10;
  const coverageAfter: number[] = [];
  for (let index = 0; index < strokeCount; index += 1) {
    // Each stroke starts at the same point but reaches further right, so
    // every one adds new, distinguishable coverage instead of repainting an
    // already-marked area.
    await paintGrowingStroke(page, 0.15 + index * 0.075);
    const readout = page.getByText(/^Edit area/);
    await expect(readout).toBeVisible();
    coverageAfter.push(parseCoverage(await readout.textContent()));
  }
  // Sanity check: coverage must actually have grown each step, or this test
  // would pass trivially without ever exercising undo.
  expect(new Set(coverageAfter).size).toBe(strokeCount);
  for (let index = 1; index < strokeCount; index += 1) {
    expect(coverageAfter[index]).toBeGreaterThan(coverageAfter[index - 1]);
  }

  const restored: number[] = [];
  for (let index = strokeCount - 1; index >= 0; index -= 1) {
    await page.keyboard.press('Control+z');
    await expect(page.getByText(/^Edit area/)).toBeVisible();
    restored.push(parseCoverage(await page.getByText(/^Edit area/).textContent()));
  }

  // Each undo restores the exact pre-stroke pixels from its patch (plan P4),
  // and a patch-less replay now retraces a command through the same
  // segment-by-segment `stroke()` calls live painting made (plan's "按段精确
  // 重放") instead of one continuous path — so the recounted coverage matches
  // what the incremental tracker reported at paint time exactly, no
  // tolerance needed. restored[k] is the state after undoing the (k + 1)-th
  // last stroke, so it corresponds to coverageAfter[strokeCount - 2 - k].
  expect(restored[strokeCount - 1]).toBe(0);
  for (let index = 0; index < strokeCount - 1; index += 1) {
    expect(restored[index]).toBe(coverageAfter[strokeCount - 2 - index]);
  }
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeDisabled();
});

test('reopening a saved mask restores its smoothing radius without re-stacking it', async ({ page }) => {
  // Regression for U2: reopening used to import the already-processed export
  // as fresh marks and reset smoothing to 0, so the slider forgot the radius
  // the mask was drawn with and a second save could blur an already-blurred
  // selection. Region gap filling is disabled here so the readout reflects
  // only the base (smoothed) selection, not an auto-fill pass on top of it.
  await disableRegionProcessing(page);
  await loadApp(page);
  await uploadSizedPrimary(page, LARGE_PNG, 'reedit.png');
  await zoomToActualSize(page);
  await setBrushSize(page, 32);
  await openRegionPanel(page);
  await setSmoothing(page, 12);
  await page.getByRole('button', { name: 'Region processing' }).click();
  await paintStroke(page);

  const readout = () => page.getByText(/^Edit area/);
  const coverageText = async () => parseCoverage(await readout().textContent());
  await expect.poll(coverageText).toBeGreaterThan(0);
  const coverageBeforeSave = await coverageText();

  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();

  await openMaskEditor(page, 'reedit.png');
  await expect.poll(coverageText).toBe(coverageBeforeSave);
  await openRegionPanel(page);
  const smoothingSlider = page
    .locator('label')
    .filter({ hasText: 'Edge smoothing' })
    .locator('input[type="range"]');
  await expect(smoothingSlider).toHaveValue('12');

  // Saving again without further edits must round-trip exactly: reapplying a
  // non-idempotent option (smoothing) on top of itself would have changed it.
  await page.getByRole('button', { name: 'Save selection' }).click();
  await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
  await openMaskEditor(page, 'reedit.png');
  await expect.poll(coverageText).toBe(coverageBeforeSave);
});

/**
 * Overlay-canvas backing pixel for image coordinate (ix, iy). The overlay is a
 * viewport-sized sibling of the zoom layer, so the mapping runs through the
 * displayed image rect.
 */
async function overlayPixelForImage(page: Page, size: number, ix: number, iy: number) {
  const image = await maskImageBox(page);
  const canvas = await page.locator(MASK_CANVAS).boundingBox();
  if (!canvas) throw new Error('mask canvas has no layout box');
  const dpr = await page.evaluate(() => window.devicePixelRatio || 1);
  const scale = image.width / size;
  return {
    x: Math.round((image.x - canvas.x + ix * scale) * dpr),
    y: Math.round((image.y - canvas.y + iy * scale) * dpr)
  };
}

async function overlayAlphaAtImage(page: Page, size: number, ix: number, iy: number) {
  const point = await overlayPixelForImage(page, size, ix, iy);
  return (await canvasPixel(page, point.x, point.y))[3];
}

async function uploadTwoTonePrimary(page: Page, name = 'snap-source.png') {
  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name, mimeType: 'image/png', buffer: TWO_TONE_PNG }]);
  await expect(page.getByRole('button', { name: `Preview ${name}` })).toBeVisible();
}

test('edge snapping pulls a rectangle corner onto the image edge', async ({ page }) => {
  await loadApp(page);
  await uploadTwoTonePrimary(page);
  await openMaskEditor(page, 'snap-source.png');

  await page.getByRole('button', { name: 'Rectangle' }).click();
  await page.getByRole('button', { name: 'Path snap', exact: true }).click();

  // Drag from just left of the x=32 step into the white half. The anchor at
  // image x~25.6 is inside the snap radius of the edge, so the selection must
  // start at the edge (~31.5) rather than where the pointer went down.
  await canvasStrokePaths(page, [
    [
      { x: 0.4, y: 0.25 },
      { x: 0.75, y: 0.75 }
    ]
  ]);

  // x=29 sits left of the snapped boundary but right of the raw pointer anchor.
  await expect.poll(async () => overlayAlphaAtImage(page, 64, 29, 32)).toBe(0);
  expect(await overlayAlphaAtImage(page, 64, 40, 32)).toBeGreaterThan(0);
});

test('without snapping the same drag follows the pointer', async ({ page }) => {
  await loadApp(page);
  await uploadTwoTonePrimary(page);
  await openMaskEditor(page, 'snap-source.png');

  await page.getByRole('button', { name: 'Rectangle' }).click();
  await canvasStrokePaths(page, [
    [
      { x: 0.4, y: 0.25 },
      { x: 0.75, y: 0.75 }
    ]
  ]);

  // Snapping is off by default, so the paint starts under the pointer.
  expect(await overlayAlphaAtImage(page, 64, 29, 32)).toBeGreaterThan(0);
});

test('the snap toggle is remembered across editor sessions', async ({ page }) => {
  await loadApp(page);
  await uploadTwoTonePrimary(page);
  await openMaskEditor(page, 'snap-source.png');

  await page.getByRole('button', { name: 'Rectangle' }).click();
  const snapButton = page.getByRole('button', { name: 'Path snap', exact: true });
  await expect(snapButton).toHaveAttribute('aria-pressed', 'false');
  await snapButton.click();
  await expect(snapButton).toHaveAttribute('aria-pressed', 'true');

  await page.keyboard.press('Escape');
  await expect(EDITOR_DIALOG(page)).toHaveCount(0);
  await openMaskEditor(page, 'snap-source.png');

  await page.getByRole('button', { name: 'Rectangle' }).click();
  await expect(page.getByRole('button', { name: 'Path snap', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
});

test('the ellipse tool fills an oval inside the dragged box', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Ellipse' }).click();
  await canvasStrokePaths(page, [
    [
      { x: 0.25, y: 0.25 },
      { x: 0.75, y: 0.75 }
    ]
  ]);

  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();
  // The box spans image 16..48, so its center is inside the oval while its
  // corner falls outside it.
  expect(await overlayAlphaAtImage(page, 64, 32, 32)).toBeGreaterThan(0);
  expect(await overlayAlphaAtImage(page, 64, 17, 17)).toBe(0);
});

test('the coverage readout updates while a stroke is still in flight', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();

  const box = await maskImageBox(page);
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.5, { steps: 8 });

  // Still holding the pointer down: the readout must already reflect the paint.
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();
  await page.mouse.up();
});

test('escape cancels a shape drag instead of closing the editor', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page.getByRole('button', { name: 'Rectangle' }).click();
  const box = await maskImageBox(page);
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.7, { steps: 5 });

  await page.keyboard.press('Escape');
  await expect(EDITOR_DIALOG(page)).toBeVisible();

  await page.mouse.up();
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeDisabled();
});

test('the smoothed preview matches a full recompute at the same radius', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  // The smoothing slider lives in the region-processing panel now.
  await page.getByRole('button', { name: 'Region processing' }).click();
  const smoothing = page
    .locator('label')
    .filter({ hasText: 'Edge smoothing' })
    .locator('input[type="range"]');
  const setRadius = (px: number) =>
    smoothing.evaluate((element, value) => {
      const input = element as HTMLInputElement;
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, px);
  const readout = async () => parseCoverage(await page.getByText(/^Edit area/).textContent());

  await setRadius(8);
  await paintStroke(page);
  // A single stroke covers far less than the 25% gate, so this repaint runs on
  // the dirty-region path rather than the full-frame one.
  await expect.poll(readout).toBeGreaterThan(0);
  const region = await readout();

  // Changing the radius forces two full-frame repaints; the second one is at
  // the original radius, so it must agree with the dirty-region result.
  await setRadius(4);
  await setRadius(8);
  await expect
    .poll(async () => Math.abs((await readout()) - region))
    .toBeLessThanOrEqual(0.1);
});

/** Open the region-processing panel (edge smoothing lives in it now). */
async function openRegionPanel(page: Page) {
  await page.getByRole('button', { name: 'Region processing' }).click();
}

/** Solve the region-processing panel's smoothing slider, in image pixels. */
async function setSmoothing(page: Page, px: number) {
  const smoothing = page
    .locator('label')
    .filter({ hasText: 'Edge smoothing' })
    .locator('input[type="range"]');
  await smoothing.evaluate((element, value) => {
    const input = element as HTMLInputElement;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, px);
}

test('edge smoothing keeps a thin stroke it used to delete outright', async ({ page }) => {
  // Regression for G5: blurring and thresholding at 50% is not extensive, so a
  // 12px stroke under a 16px smoothing used to survive at 0%. The smoothed
  // view is the union of the blur and the marks now, so it cannot be erased.
  await loadApp(page);
  await uploadSizedPrimary(page, LARGE_PNG, 'thin.png');
  await zoomToActualSize(page);
  await setBrushSize(page, 12);
  await openRegionPanel(page);
  await setSmoothing(page, 16);
  await page.getByRole('button', { name: 'Region processing' }).click();

  const center = 1024;
  await paintImagePath(page, 2048, [
    { x: center - 64, y: center },
    { x: center + 64, y: center }
  ]);

  const mask = await exportedMask(page, 'thin stroke prompt');
  const alphas = await maskAlphas(page, mask, [
    { x: center, y: center },
    { x: center - 40, y: center },
    { x: center, y: center - 60 }
  ]);
  expect(alphas[0]).toBe(0);
  expect(alphas[1]).toBe(0);
  expect(alphas[2]).toBe(255);
});

test('coverage stays exact when a stroke follows another within the debounce', async ({ page }) => {
  // Regression for P2: a segment box that omitted the point it was drawn from
  // could miss the midpoint of a pointer jump, leaving the smoothed preview
  // and the coverage behind a full recompute until the next full repaint.
  await loadApp(page);
  await uploadSizedPrimary(page, LARGE_PNG, 'jump.png');
  await zoomToActualSize(page);
  await setBrushSize(page, 32);
  await openRegionPanel(page);
  await setSmoothing(page, 12);
  await page.getByRole('button', { name: 'Region processing' }).click();

  const readout = async () => parseCoverage(await page.getByText(/^Edit area/).textContent());
  const center = 1024;
  await paintImagePath(page, 2048, [
    { x: center - 300, y: center - 150 },
    { x: center - 100, y: center - 150 }
  ]);
  // Immediately again, in segments far longer than the brush is wide, so the
  // debounced smoothing sync fires in the middle of a stroke whose segment
  // boxes would otherwise miss the point they are drawn from.
  await paintImagePath(page, 2048, [
    { x: center + 150, y: center },
    { x: center + 400, y: center }
  ]);

  await expect.poll(readout).toBeGreaterThan(0);
  const incremental = await readout();

  // Forcing two full-frame repaints must agree with the incremental result.
  await openRegionPanel(page);
  await setSmoothing(page, 6);
  await setSmoothing(page, 12);
  await page.getByRole('button', { name: 'Region processing' }).click();
  await expect
    .poll(async () => Math.abs((await readout()) - incremental))
    .toBeLessThanOrEqual(0.1);
});

// ---------------------------------------------------------------------------
// Region gap filling (plan Phase 2) and object-edge snapping (plan Phase 3).
// ---------------------------------------------------------------------------

/** Upload a primary of a specific size and open the editor on it. */
async function uploadSizedPrimary(page: Page, buffer: Buffer, name: string) {
  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name, mimeType: 'image/png', buffer }]);
  await expect(page.getByRole('button', { name: `Preview ${name}` })).toBeVisible();
  await openMaskEditor(page, name);
}

/** One screen pixel per image pixel, so tests can reason in image space. */
async function zoomToActualSize(page: Page) {
  await page.getByRole('button', { name: '100% (original pixels)' }).click();
  await expect(page.getByRole('button', { name: '100% (original pixels)' })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
}

/** Solve the brush-size slider to a screen-pixel width. */
async function setBrushSize(page: Page, px: number) {
  const slider = page
    .locator('label')
    .filter({ hasText: 'Brush' })
    .locator('input[type="range"]');
  await slider.evaluate((element, value) => {
    const input = element as HTMLInputElement;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, px);
}

/** Paint one stroke through a list of image-space points (at 100% zoom). */
async function paintImagePath(page: Page, imageSize: number, points: { x: number; y: number }[]) {
  const box = await maskImageBox(page);
  const toScreen = (point: { x: number; y: number }) => ({
    x: box.x + box.width * (point.x / imageSize),
    y: box.y + box.height * (point.y / imageSize)
  });
  const first = toScreen(points[0]);
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  for (const point of points.slice(1)) {
    const next = toScreen(point);
    await page.mouse.move(next.x, next.y, { steps: 4 });
  }
  await page.mouse.up();
}

/**
 * Apply the current selection and submit the edit, returning the mask PNG the
 * request carried. The editor waits for the newest gap-fill result before it
 * renders, so this is the exact artifact the readout promised.
 */
async function exportedMask(page: Page, prompt: string): Promise<Buffer> {
  await page.getByRole('button', { name: 'Save selection' }).click();
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill(prompt);
  const editRequestPromise = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/edits'
  );
  await page.getByRole('button', { name: 'Edits' }).click();
  const requestBody = (await editRequestPromise).postDataBuffer();
  const maskPng = requestBody ? pngFromMultipart(requestBody, 'mask') : null;
  if (!maskPng) throw new Error('the edit request carried no mask');
  return maskPng;
}

/** Decode a mask PNG in the page and read the alpha of specific pixels. */
async function maskAlphas(page: Page, maskPng: Buffer, points: { x: number; y: number }[]) {
  return page.evaluate(
    async ({ base64, samples }) => {
      const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('no 2d context');
      context.drawImage(bitmap, 0, 0);
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      return samples.map(({ x, y }) => {
        const px = Math.min(canvas.width - 1, Math.max(0, x));
        const py = Math.min(canvas.height - 1, Math.max(0, y));
        return data[(py * canvas.width + px) * 4 + 3];
      });
    },
    { base64: maskPng.toString('base64'), samples: points }
  );
}

/** One horizontal run of a decoded mask's alpha values. */
async function maskRow(page: Page, maskPng: Buffer, y: number, x0: number, x1: number) {
  return page.evaluate(
    async ({ base64, row, from, to }) => {
      const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('no 2d context');
      context.drawImage(bitmap, 0, 0);
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const values: number[] = [];
      for (let x = from; x <= to; x += 1) values.push(data[(row * canvas.width + x) * 4 + 3]);
      return values;
    },
    { base64: maskPng.toString('base64'), row: y, from: x0, to: x1 }
  );
}

/** Editable-area ratio of an exported mask, plus a digest of every alpha. */
async function maskStats(page: Page, maskPng: Buffer) {
  return page.evaluate(async (base64: string) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('no 2d context');
    context.drawImage(bitmap, 0, 0);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let transparent = 0;
    let hash = 2166136261;
    for (let index = 3; index < data.length; index += 4) {
      const value = data[index];
      if (value === 0) transparent += 1;
      hash = Math.imul(hash ^ value, 16777619) >>> 0;
    }
    return { coverage: transparent / (canvas.width * canvas.height), hash, transparent };
  }, maskPng.toString('base64'));
}

test('auto-fill closes the seam between two parallel strokes by default', async ({ page }) => {
  const size = 2048;
  await loadApp(page);
  await uploadSizedPrimary(page, LARGE_PNG, 'seam.png');
  await zoomToActualSize(page);
  await setBrushSize(page, 4);

  const center = size / 2;
  // Two vertical strokes 8px apart: with a 4px brush that leaves a 3px seam,
  // well inside the closing radius (6px at this size).
  await paintImagePath(page, size, [
    { x: center - 4, y: center - 120 },
    { x: center - 4, y: center + 120 }
  ]);
  await paintImagePath(page, size, [
    { x: center + 4, y: center - 120 },
    { x: center + 4, y: center + 120 }
  ]);

  // Wait for the gap-fill pass to land before exporting, so the test measures
  // the pipeline's answer rather than a race with the debounce.
  const readout = page.getByText(/^Edit area/);
  await expect(readout).not.toContainText('…');
  const mask = await exportedMask(page, 'seam prompt');
  // The exact seam position depends on sub-pixel pointer rounding, so the run
  // between the outermost marked pixels is what has to be solid.
  const row = await maskRow(page, mask, center - 60, center - 20, center + 20);
  const first = row.indexOf(0);
  const last = row.length - 1 - [...row].reverse().indexOf(0);
  expect(first).toBeGreaterThanOrEqual(0);
  expect(last).toBeGreaterThan(first);
  expect(row.slice(first, last + 1)).toEqual(new Array(last - first + 1).fill(0));
});

test('auto-fill leaves the seam alone when it is switched off', async ({ page }) => {
  const size = 2048;
  await disableRegionProcessing(page);
  await loadApp(page);
  await uploadSizedPrimary(page, LARGE_PNG, 'seam-off.png');
  await zoomToActualSize(page);
  await setBrushSize(page, 4);

  const center = size / 2;
  await paintImagePath(page, size, [
    { x: center - 4, y: center - 120 },
    { x: center - 4, y: center + 120 }
  ]);
  await paintImagePath(page, size, [
    { x: center + 4, y: center - 120 },
    { x: center + 4, y: center + 120 }
  ]);

  const mask = await exportedMask(page, 'seam off prompt');
  // The same run that is solid with the pass enabled keeps its gap without it.
  const row = await maskRow(page, mask, center - 60, center - 20, center + 20);
  const first = row.indexOf(0);
  const last = row.length - 1 - [...row].reverse().indexOf(0);
  expect(first).toBeGreaterThanOrEqual(0);
  expect(last).toBeGreaterThan(first);
  expect(row.slice(first, last + 1).some((alpha) => alpha === 255)).toBe(true);
});

test('auto-fill pulls a stroke that stops short of the frame out to the edge', async ({ page }) => {
  await loadApp(page);
  await uploadSizedPrimary(page, MEDIUM_PNG, 'frame-snap.png');

  // A rectangle that stops a few pixels short of the bottom edge; the border
  // snap reach at this size is 5px.
  await page.getByRole('button', { name: 'Rectangle' }).click();
  const box = await maskImageBox(page);
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.99, { steps: 6 });
  await page.mouse.up();

  const mask = await exportedMask(page, 'frame prompt');
  const alphas = await maskAlphas(page, mask, [
    { x: 256, y: 511 },
    { x: 256, y: 505 }
  ]);
  expect(alphas[0]).toBe(0);
  expect(alphas[1]).toBe(0);
});

/** Paint a small circle with the current brush, leaving a pinhole in it. */
async function paintRing(page: Page, imageSize: number, center: number, radius: number) {
  const box = await maskImageBox(page);
  const at = (angle: number) => ({
    x: box.x + box.width * ((center + Math.cos(angle) * radius) / imageSize),
    y: box.y + box.height * ((center + Math.sin(angle) * radius) / imageSize)
  });
  const steps = 9;
  const first = at(0);
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  for (let step = 1; step <= steps; step += 1) {
    const point = at((step / steps) * Math.PI * 2);
    await page.mouse.move(point.x, point.y, { steps: 2 });
  }
  await page.mouse.up();
}

test('auto-fill closes the pinhole a wide brush leaves inside a ring', async ({ page }) => {
  await loadApp(page);
  await uploadSizedPrimary(page, MEDIUM_PNG, 'pinhole.png');
  // At this size the hole fill reach is 8px, and a 24px brush on a 20px radius
  // leaves a hole of about 5px.
  await setBrushSize(page, 24);
  await paintRing(page, 512, 256, 20);

  const mask = await exportedMask(page, 'pinhole prompt');
  // The whole run through the ring is editable, pinhole included.
  const row = await maskRow(page, mask, 256, 246, 266);
  expect(row).toEqual(new Array(row.length).fill(0));
});

test('the same pinhole survives when the pass is switched off', async ({ page }) => {
  await disableRegionProcessing(page);
  await loadApp(page);
  await uploadSizedPrimary(page, MEDIUM_PNG, 'pinhole-off.png');
  await setBrushSize(page, 24);
  await paintRing(page, 512, 256, 20);

  const mask = await exportedMask(page, 'pinhole off prompt');
  const row = await maskRow(page, mask, 256, 246, 266);
  expect(row.some((alpha) => alpha === 255)).toBe(true);
  expect(row.some((alpha) => alpha === 0)).toBe(true);
});

test('auto-fill never restores a hole the eraser cut (I2)', async ({ page }) => {
  await loadApp(page);
  await uploadSizedPrimary(page, SMALL_PNG, 'i2.png');

  // A rectangle near the frame (so the region pass has plenty to add) with a
  // 20px hole subtracted out of the middle of it.
  await page.getByRole('button', { name: 'Rectangle' }).click();
  const box = await maskImageBox(page);
  const at = (fx: number, fy: number) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
  const drag = async (from: { x: number; y: number }, to: { x: number; y: number }) => {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 5 });
    await page.mouse.up();
  };
  await drag(at(0.05, 0.05), at(0.95, 0.982));
  await page.getByRole('button', { name: 'Subtract' }).click();
  await drag(at(0.45, 0.45), at(0.55, 0.55));

  const mask = await exportedMask(page, 'protection prompt');
  const alphas = await maskAlphas(page, mask, [{ x: 128, y: 128 }]);
  // The hole survives: the eraser put it in the protection set, so the hole
  // filler is not allowed to put it back — whatever the closing radius is.
  expect(alphas[0]).toBe(255);
});

test('the readout reports the auto-fill delta and matches the exported mask', async ({ page }) => {
  await loadApp(page);
  await uploadSizedPrimary(page, MEDIUM_PNG, 'delta.png');

  // A rectangle that stops inside the frame: the border snap adds the strip
  // between it and the bottom edge, and the readout reports that as a delta.
  await page.getByRole('button', { name: 'Rectangle' }).click();
  const box = await maskImageBox(page);
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.99, { steps: 6 });
  await page.mouse.up();

  const readout = page.getByText(/^Edit area/);
  await expect(readout).toContainText('auto-fill');
  const numbers = ((await readout.textContent()) ?? '').match(/[\d.]+/g)?.map(Number) ?? [];
  expect(numbers.length).toBeGreaterThanOrEqual(2);

  const mask = await exportedMask(page, 'delta prompt');
  const stats = await maskStats(page, mask);
  expect(Math.abs(stats.coverage * 100 - (numbers[0] + numbers[1]))).toBeLessThanOrEqual(0.1);
});

test('editor readout, exported PNG and backend gallery coverage agree within 0.1pp', async ({ page }) => {
  await loadApp(page);
  await uploadSizedPrimary(page, MEDIUM_PNG, 'coverage-stack.png');
  await page.getByRole('button', { name: 'Rectangle' }).click();
  const box = await maskImageBox(page);
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.8, { steps: 6 });
  await page.mouse.up();
  const readout = page.getByText(/^Edit area/);
  await expect(readout).not.toContainText('…');
  const readoutNumbers = ((await readout.textContent()) ?? '').match(/[\d.]+/g)?.map(Number) ?? [];
  const readoutPct = readoutNumbers.reduce((sum, number) => sum + number, 0);
  const mask = await exportedMask(page, 'cross-stack coverage prompt');
  const exportedPct = (await maskStats(page, mask)).coverage * 100;

  const repo = resolve(process.cwd(), '..');
  const response = execFileSync(resolve(repo, '.venv/bin/python'), [
    resolve(repo, 'backend/tests/support/browser_mask_coverage.py')
  ], {
    cwd: repo,
    input: JSON.stringify({ image: MEDIUM_PNG.toString('base64'), mask: mask.toString('base64') }),
    encoding: 'utf8',
    timeout: 20_000
  });
  const backendPct = (JSON.parse(response) as { mask_coverage: number }).mask_coverage * 100;
  expect(Math.abs(readoutPct - exportedPct)).toBeLessThanOrEqual(0.1);
  expect(Math.abs(exportedPct - backendPct)).toBeLessThanOrEqual(0.1);
});

test('the auto-fill toggle is remembered across editor sessions', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  const toggle = page.getByRole('button', { name: 'Fill gaps' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');

  await page.keyboard.press('Escape');
  await expect(EDITOR_DIALOG(page)).toHaveCount(0);
  await openMaskEditor(page);

  await expect(page.getByRole('button', { name: 'Fill gaps' })).toHaveAttribute(
    'aria-pressed',
    'false'
  );
});

test('the export is identical without a worker', async ({ page, context }) => {
  const run = async (target: Page, disableWorker: boolean) => {
    const workers: string[] = [];
    target.on('worker', (worker) => workers.push(worker.url()));
    if (disableWorker) {
      await target.addInitScript(() => {
        delete (window as unknown as { Worker?: unknown }).Worker;
      });
    }
    await loadApp(target);
    await uploadSizedPrimary(target, LARGE_PNG, `worker-${disableWorker}.png`);
    if (!disableWorker) await expect.poll(() => workers.length).toBeGreaterThan(0);
    await zoomToActualSize(target);
    await paintStroke(target);
    const readout = parseCoverage(await target.getByText(/^Edit area/).textContent());
    const stats = await maskStats(target, await exportedMask(target, 'worker prompt'));
    return { readout, stats, workers };
  };

  const withWorker = await run(page, false);
  const withoutWorker = await run(await context.newPage(), true);

  expect(withWorker.stats.transparent).toBeGreaterThan(0);
  expect(withWorker.workers.some((url) => /maskRegion\.worker/.test(url))).toBe(true);
  expect(withoutWorker.workers).toEqual([]);
  expect(withoutWorker.stats).toEqual(withWorker.stats);
  expect(Math.abs(withoutWorker.readout - withWorker.readout)).toBeLessThanOrEqual(0.1);
});

test('edge snapping grows a selection onto a nearby object edge', async ({ page }) => {
  await loadApp(page);
  await uploadSizedPrimary(page, EDGE_PNG, 'object-edge.png');

  // The dark square spans 70..130 in a 200px image. Paint a smaller box inside
  // it that stops 10px short of the left edge, then let the watershed follow
  // the edge out.
  await page.getByRole('button', { name: 'Rectangle' }).click();
  const box = await maskImageBox(page);
  const at = (value: number) => ({
    x: box.x + box.width * (value / 200),
    y: box.y + box.height * (value / 200)
  });
  const from = at(80);
  const to = at(115);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 5 });
  await page.mouse.up();

  await page.getByRole('button', { name: 'Region processing' }).click();
  await page.getByRole('checkbox', { name: 'Follow object edges' }).check();
  // The reach has to cover the gap to the edge for the flood to bridge it.
  const reach = page.getByRole('slider', { name: /Edge reach/ });
  await reach.evaluate((element) => {
    const input = element as HTMLInputElement;
    input.value = '32';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  // Close the panel again so it cannot sit over the canvas.
  await page.getByRole('button', { name: 'Region processing' }).click();

  // The selection must have grown from 80 out onto the object edge at 70, and
  // must not have crossed it.
  const mask = await exportedMask(page, 'object edge prompt');
  const alphas = await maskAlphas(page, mask, [
    { x: 71, y: 100 },
    { x: 66, y: 100 },
    { x: 55, y: 100 }
  ]);
  expect(alphas[0]).toBe(0);
  expect(alphas[1]).toBe(255);
  expect(alphas[2]).toBe(255);
});
