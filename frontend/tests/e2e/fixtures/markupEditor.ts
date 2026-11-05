import { expect, type Page } from '@playwright/test';
import { loadApp } from './mockApi';

// Shared drivers for the markup editor browser tests: they operate the editor
// like a user would and read back the decoded PNG pixels from the request body.

export const MODEL = 'gpt-image-2.6-flare';
export const SOURCE_SIZE = 256;

export async function makeSourcePng(page: Page, width = SOURCE_SIZE, height = SOURCE_SIZE): Promise<Buffer> {
  const bytes = await page.evaluate(
    async ([w, h]) => {
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#d9e9dc';
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = '#347a60';
      ctx.fillRect(w / 4, h / 4, w / 2, h / 2);
      const blob = await new Promise<Blob>((resolve) => canvas.toBlob((b) => resolve(b!), 'image/png'));
      return Array.from(new Uint8Array(await blob.arrayBuffer()));
    },
    [width, height]
  );
  return Buffer.from(bytes);
}

export async function setupEdit(page: Page, options: Parameters<typeof loadApp>[1] = {}) {
  await loadApp(page, options);
  await page
    .locator('section')
    .filter({ has: page.locator('#prompt') })
    .getByRole('combobox', { name: 'Model', exact: true })
    .selectOption(MODEL);
  const source = await makeSourcePng(page);
  await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: source });
  await expect(page.getByRole('button', { name: 'Draw markup', exact: true })).toBeVisible();
  return source;
}

export const editorDialog = (page: Page) => page.getByRole('dialog', { name: /^Markup/ });
export const editorCanvas = (page: Page) => page.getByLabel('Markup canvas', { exact: true });

/** Waits for the enter/exit animation, which scales the dialog, to finish. */
export async function settle(page: Page) {
  await page.evaluate(async () => {
    const finite = document
      .getAnimations()
      .filter((animation) => Number.isFinite(animation.effect?.getComputedTiming().endTime as number));
    await Promise.all(finite.map((animation) => animation.finished.catch(() => undefined)));
  });
}

export async function openEditor(page: Page, name = 'Draw markup') {
  await page.getByRole('button', { name, exact: true }).click();
  await expect(editorDialog(page)).toBeVisible();
  await expect(editorCanvas(page)).toBeVisible();
  await expect(editorDialog(page).getByRole('status')).toHaveCount(0);
  await settle(page);
}

/** The editor fits the image into the stage (12px padding) and centers it. */
export async function imageToScreen(page: Page, imageX: number, imageY: number, size = SOURCE_SIZE) {
  const box = await editorCanvas(page).boundingBox();
  if (!box) throw new Error('markup canvas has no layout box');
  const scale = Math.min((box.width - 24) / size, (box.height - 24) / size);
  return {
    x: box.x + (box.width - size * scale) / 2 + imageX * scale,
    y: box.y + (box.height - size * scale) / 2 + imageY * scale
  };
}

export async function dragOnImage(page: Page, from: [number, number], to: [number, number], steps = 10) {
  const a = await imageToScreen(page, from[0], from[1]);
  const b = await imageToScreen(page, to[0], to[1]);
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(b.x, b.y, { steps });
  await page.mouse.up();
}

export function multipartPart(body: Buffer, field: string): Buffer | null {
  const boundaryEnd = body.indexOf('\r\n');
  const boundary = body.subarray(0, boundaryEnd);
  const at = body.indexOf(`name="${field}"`);
  if (at < 0) return null;
  const start = body.indexOf('\r\n\r\n', at) + 4;
  const end = body.indexOf(Buffer.concat([Buffer.from('\r\n'), boundary]), start);
  return body.subarray(start, end);
}

export async function pixelsOf(page: Page, png: Buffer, points: Array<[number, number]>) {
  return page.evaluate(
    async ({ base64, samples }) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      const result = {
        width: bitmap.width,
        height: bitmap.height,
        rgba: samples.map(([x, y]: [number, number]) => Array.from(ctx.getImageData(x, y, 1, 1).data))
      };
      bitmap.close();
      return result;
    },
    { base64: png.toString('base64'), samples: points }
  );
}

export async function submitEdit(page: Page, prompt = 'Follow the annotation') {
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill(prompt);
  const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/edits' && r.method() === 'POST');
  await page.getByRole('button', { name: 'Edits', exact: true }).click();
  const body = (await submitted).postDataBuffer();
  if (!body) throw new Error('edit request had no body');
  return body;
}

export async function alphaCount(page: Page, png: Buffer, box: { x: number; y: number; width: number; height: number }) {
  return page.evaluate(
    async ({ base64, region }) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      const data = ctx.getImageData(region.x, region.y, region.width, region.height).data;
      let count = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i] > 0) count += 1;
      bitmap.close();
      return count;
    },
    { base64: png.toString('base64'), region: box }
  );
}

export async function applyAndSubmit(page: Page) {
  await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
  await expect(editorDialog(page)).toHaveCount(0);
  const body = await submitEdit(page);
  return multipartPart(body, 'markup')!;
}

export const tool = (page: Page, name: string) => editorDialog(page).getByRole('button', { name, exact: true });

