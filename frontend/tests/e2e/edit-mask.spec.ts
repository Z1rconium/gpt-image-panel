import { expect, test, type Page } from '@playwright/test';
import { loadApp } from './fixtures/mockApi';

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
  await page.getByRole('button', { name: `Edit mask for ${label}` }).click();
  await expect(page.getByRole('dialog', { name: /Edit mask/ })).toBeVisible();
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
  await expect(page.getByRole('button', { name: 'Apply mask' })).toBeDisabled();

  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.getByRole('button', { name: 'Apply mask' }).click();
  await expect(page.getByRole('dialog', { name: /Edit mask/ })).toHaveCount(0);
  await expect(page.getByText(/^Mask \d+\.\d%$/)).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Mask applied');
});

test('adding another reference image keeps the applied mask', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Apply mask' }).click();
  await expect(page.getByText(/^Mask \d+\.\d%$/)).toBeVisible();

  await page
    .getByLabel('Upload edit image')
    .setInputFiles([{ name: 'second.png', mimeType: 'image/png', buffer: SOURCE_PNG }]);

  await expect(page.getByRole('button', { name: 'Preview second.png' })).toBeVisible();
  await expect(page.getByText(/^Mask \d+\.\d%$/)).toBeVisible();
});

test('removing the primary image discards the mask and warns', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Apply mask' }).click();
  await expect(page.getByText(/^Mask \d+\.\d%$/)).toBeVisible();

  await page.getByRole('button', { name: 'Remove mask-source.png' }).click();

  await expect(page.getByText(/^Mask \d+\.\d%$/)).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Mask cleared because the primary image changed');
});

test('submitting an edit attaches the applied mask as mask.png', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);
  await paintStroke(page);
  await page.getByRole('button', { name: 'Apply mask' }).click();

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('masked edit prompt');
  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const body = (await editRequestPromise).postDataBuffer()?.toString('latin1') || '';

  expect(body).toContain('name="mask"');
  expect(body).toContain('filename="mask.png"');
  expect(body).toContain('filename="mask-source.png"');
});

test('uploading a valid mask PNG imports its transparent area', async ({ page }) => {
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await page
    .locator(MASK_INPUT)
    .setInputFiles([{ name: 'mask.png', mimeType: 'image/png', buffer: MASK_PNG }]);

  await expect(page.getByText('Edit area 25.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply mask' })).toBeEnabled();
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
  await expect(page.getByRole('dialog', { name: /Edit mask/ })).toHaveCount(0);

  await openMaskEditor(page);
  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.keyboard.press('Control+z');
  await expect(page.getByText('Edit area 0.0%')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply mask' })).toBeDisabled();
});

test('mask editor stays usable at a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 });
  await loadApp(page);
  await uploadPrimary(page);
  await openMaskEditor(page);

  await expect(page.getByRole('button', { name: 'Brush' })).toBeVisible();
  await paintStroke(page);
  await expect(page.getByText(/Edit area [1-9]/)).toBeVisible();

  await page.getByRole('button', { name: 'Apply mask' }).click();
  await expect(page.getByText(/^Mask \d+\.\d%$/)).toBeVisible();
});
