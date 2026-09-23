import { deflateSync } from 'node:zlib';
import { expect, test, type Page } from '@playwright/test';
import { job, loadApp } from './fixtures/mockApi';

// 64x64 source and matching 64x64 mask (transparent 32x32 center), plus a
// 32x32 mask that intentionally mismatches the primary image.
const SOURCE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAYklEQVR4nO3PMQ0AIADAMEAN/lUgCxEcDcmqYJtn7/GzpQNeNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaBdCH8BmPRLpIsAAAAASUVORK5CYII=',
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

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;
  for (let index = 0; index < buffer.length; index += 1) {
    crc = CRC_TABLE[(crc ^ buffer[index]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

/** A solid-color RGB PNG; used to exercise large-image editor sizing. */
function solidPng(width: number, height: number): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const raw = Buffer.alloc((1 + width * 3) * height);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0))
  ]);
}

const HUGE_PNG = solidPng(4096, 4096);

/** A 64x64 RGB PNG with a hard black/white vertical step at x = 32. */
function twoTonePng(width: number, height: number): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const raw = Buffer.alloc((1 + width * 3) * height);
  let offset = 0;
  for (let y = 0; y < height; y += 1) {
    raw[offset] = 0;
    offset += 1;
    for (let x = 0; x < width; x += 1) {
      const value = x < width / 2 ? 0 : 255;
      raw[offset] = value;
      raw[offset + 1] = value;
      raw[offset + 2] = value;
      offset += 3;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0))
  ]);
}

const TWO_TONE_PNG = twoTonePng(64, 64);

const MASK_CANVAS = 'canvas[aria-label="Mask canvas"]';
const MASK_INPUT = 'input[type="file"][accept="image/png"]';
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

test('undo across a checkpoint boundary restores the prior coverage', async ({ page }) => {
  // maskDocument.ts snapshots an undo checkpoint every 8 committed commands
  // (CHECKPOINT_INTERVAL); ten strokes and ten matching undos exercise both
  // the checkpoint-restore path and the from-scratch path below it.
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

  // Undo restores from the nearest checkpoint and replays the tail. A replayed
  // stroke is traced as one path while live painting traces it segment by
  // segment, so antialiased edge pixels land a hair differently and the
  // recounted coverage runs ~0.1-0.2pp below what the incremental tracker
  // reported at paint time. Hence the tolerance instead of string equality.
  // restored[k] is the state after undoing the (k + 1)-th last stroke, so it
  // corresponds to coverageAfter[strokeCount - 2 - k].
  expect(restored[strokeCount - 1]).toBe(0);
  for (let index = 0; index < strokeCount - 1; index += 1) {
    const expected = coverageAfter[strokeCount - 2 - index];
    expect(Math.abs(restored[index] - expected)).toBeLessThanOrEqual(0.3);
  }
  await expect(page.getByRole('button', { name: 'Save selection' })).toBeDisabled();
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
  await page.getByRole('button', { name: 'Snap', exact: true }).click();

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
  const snapButton = page.getByRole('button', { name: 'Snap', exact: true });
  await expect(snapButton).toHaveAttribute('aria-pressed', 'false');
  await snapButton.click();
  await expect(snapButton).toHaveAttribute('aria-pressed', 'true');

  await page.keyboard.press('Escape');
  await expect(EDITOR_DIALOG(page)).toHaveCount(0);
  await openMaskEditor(page, 'snap-source.png');

  await page.getByRole('button', { name: 'Rectangle' }).click();
  await expect(page.getByRole('button', { name: 'Snap', exact: true })).toHaveAttribute(
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
