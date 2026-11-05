import { expect, test, type Page } from '@playwright/test';
import { loadApp } from './fixtures/mockApi';
import {
  dragOnImage,
  editorCanvas,
  editorDialog,
  imageToScreen,
  makeSourcePng,
  MODEL,
  multipartPart,
  openEditor,
  pixelsOf,
  settle,
  setupEdit,
  SOURCE_SIZE,
  submitEdit
} from './fixtures/markupEditor';

// P4 acceptance rows that need their own browser configuration (DPR, resize,
// reduced motion, locale, theme, very large images) or the mask editor.

test.describe.configure({ timeout: 90_000 });

const FORM_MODULE = '/src/lib/features/workspace/formState.svelte.ts';
const STORE_MODULE = '/src/lib/stores/editSource.ts';

test.describe('device pixel ratio 2', () => {
  test.use({ deviceScaleFactor: 2 });

  test('a high-DPR display sharpens the viewport only; the export keeps the image size and positions', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const backing = await editorCanvas(page).evaluate((node: HTMLCanvasElement) => ({ ratio: node.width / node.clientWidth, dpr: window.devicePixelRatio }));
    expect(backing.dpr).toBe(2);
    expect(backing.ratio).toBeCloseTo(2, 1);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    const body = await submitEdit(page);
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[128, 128], [128, 90]]);
    expect(px.width).toBe(SOURCE_SIZE);
    expect(px.height).toBe(SOURCE_SIZE);
    expect(px.rgba[0]).toEqual([229, 50, 45, 255]);
    expect(px.rgba[1][3]).toBe(0);
  });
});

test.describe('viewport geometry', () => {
  test('resizing the window mid-session keeps marks on the right pixels', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 60], [216, 60]);
    await page.setViewportSize({ width: 900, height: 600 });
    await settle(page);
    await dragOnImage(page, [40, 200], [216, 200]);
    await page.setViewportSize({ width: 1400, height: 900 });
    await settle(page);
    await dragOnImage(page, [128, 90], [128, 170]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    const body = await submitEdit(page);
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[100, 60], [100, 200], [128, 130], [60, 130]]);
    expect(px.rgba[0][3]).toBe(255);
    expect(px.rgba[1][3]).toBe(255);
    expect(px.rgba[2][3]).toBe(255);
    expect(px.rgba[3][3]).toBe(0);
  });

  test('zoom scales and panning translates marks consistently (positions follow image space)', async ({ page }) => {
    await loadApp(page);
    await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption(MODEL);
    const source = await makeSourcePng(page, 512, 512);
    await page.getByLabel('Upload edit image').setInputFiles({ name: 'big.png', mimeType: 'image/png', buffer: source });
    await openEditor(page);
    const box = (await editorCanvas(page).boundingBox())!;
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    await page.mouse.move(cx, cy);
    for (let i = 0; i < 4; i += 1) await page.mouse.wheel(0, -300);
    const zoom = Number.parseInt((await editorDialog(page).getByTestId('markup-zoom').innerText()).replace('%', ''), 10) / 100;
    expect(zoom).toBeGreaterThan(1.5);

    const stroke = async (color: string) => {
      await editorDialog(page).getByRole('button', { name: color, exact: true }).click();
      await page.mouse.move(cx - 40, cy);
      await page.mouse.down();
      await page.mouse.move(cx + 40, cy, { steps: 6 });
      await page.mouse.up();
    };
    await stroke('Red');
    await page.keyboard.down('Space');
    await page.mouse.move(cx, cy + 100);
    await page.mouse.down();
    await page.mouse.move(cx - 60, cy + 60, { steps: 6 });
    await page.mouse.up();
    await page.keyboard.up('Space');
    await stroke('Blue');
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    const body = await submitEdit(page);
    const markup = multipartPart(body, 'markup')!;
    const bounds = await page.evaluate(async (base64) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      const find = (r: number, g: number, b: number) => {
        let minX = 1e9, minY = 1e9, maxX = -1, maxY = -1;
        for (let i = 0; i < data.length; i += 4) {
          if (data[i + 3] < 250 || Math.abs(data[i] - r) > 6 || Math.abs(data[i + 1] - g) > 6 || Math.abs(data[i + 2] - b) > 6) continue;
          const p = i / 4;
          const x = p % canvas.width, y = Math.floor(p / canvas.width);
          minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        }
        return { minX, minY, maxX, maxY };
      };
      return { size: [canvas.width, canvas.height], red: find(229, 50, 45), blue: find(37, 99, 235) };
    }, markup.toString('base64'));
    expect(bounds.size).toEqual([512, 512]);
    // 80 screen px of stroke length is 80 / zoom image px.
    expect(bounds.red.maxX - bounds.red.minX).toBeGreaterThan(80 / zoom - 8);
    expect(bounds.red.maxX - bounds.red.minX).toBeLessThan(80 / zoom + 14);
    // The image moved by (-60, -40) screen px between the strokes, so the same screen spot is
    // (+60, +40) / zoom further along in the image.
    expect(bounds.blue.minX - bounds.red.minX).toBeGreaterThan(60 / zoom - 3);
    expect(bounds.blue.minX - bounds.red.minX).toBeLessThan(60 / zoom + 3);
    expect(bounds.blue.minY - bounds.red.minY).toBeGreaterThan(40 / zoom - 14);
    expect(bounds.blue.minY - bounds.red.minY).toBeLessThan(40 / zoom + 14);
  });

  test('a tap leaves a dot, an interrupted pointer leaves nothing, a fully erased layer cannot be applied', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const undo = editorDialog(page).getByRole('button', { name: 'Undo' });
    const dot = await imageToScreen(page, 128, 128);
    await page.mouse.click(dot.x, dot.y);
    await expect(undo).toBeEnabled();

    // Lost pointer capture / pointercancel cancels the stroke instead of committing it.
    const a = await imageToScreen(page, 40, 200);
    const b = await imageToScreen(page, 216, 200);
    await page.mouse.move(a.x, a.y);
    await page.mouse.down();
    await page.mouse.move(b.x, b.y, { steps: 6 });
    await editorCanvas(page).evaluate((node) => node.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1, bubbles: true, pointerType: 'mouse' })));
    await page.mouse.up();
    await undo.click();
    await expect(undo).toBeDisabled();

    // Erase over everything that was drawn: the layer is empty, applying is refused and the draft stays.
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Eraser', exact: true }).click();
    await editorDialog(page).getByRole('slider').fill('21');
    for (const y of [118, 128, 138]) await dragOnImage(page, [30, y], [226, y], 6);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(page.getByText('Draw at least one mark before applying.')).toBeVisible();
    await expect(editorDialog(page)).toBeVisible();
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();
  });

  test('clicking the backdrop asks before discarding unapplied drawing, and closes a clean editor directly', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await page.mouse.click(4, 4);
    await expect(editorDialog(page)).toHaveCount(0);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await page.mouse.click(4, 4);
    const confirm = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(editorDialog(page)).toBeVisible();
  });
});

test.describe('mask and markup exclusion in the other direction', () => {
  test('applying a mask over an existing markup is confirmed; declining keeps the markup and the mask editor', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();

    await page.getByRole('button', { name: 'Edit repaint area for source.png' }).click();
    const maskDialog = page.getByRole('dialog', { name: /Repaint area/ });
    await expect(maskDialog).toBeVisible();
    await expect(page.locator('canvas[aria-label="Mask canvas"]')).toBeVisible();
    await settle(page);
    const image = (await maskDialog.locator('img').boundingBox())!;
    await page.mouse.move(image.x + image.width * 0.3, image.y + image.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(image.x + image.width * 0.7, image.y + image.height * 0.5, { steps: 8 });
    await page.mouse.up();
    await page.getByRole('button', { name: 'Save selection' }).click();
    const confirm = page.getByRole('dialog', { name: 'Replace the current markup?' });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(confirm).toHaveCount(0);
    await expect(maskDialog).toBeVisible();
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();

    await page.getByRole('button', { name: 'Save selection' }).click();
    await page.getByRole('dialog', { name: 'Replace the current markup?' }).getByRole('button', { name: 'Replace markup' }).click();
    await expect(maskDialog).toHaveCount(0);
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toHaveCount(0);
    await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
    const body = await submitEdit(page);
    expect(body.toString('latin1')).toContain('name="mask"');
    expect(body.toString('latin1')).not.toContain('name="markup"');
  });
});

test.describe('entry controls', () => {
  test('the visible Upload button opens a file chooser for the hidden input, which stays out of the tab order', async ({ page }) => {
    await setupEdit(page);
    const input = page.getByLabel('Upload / replace markup PNG');
    await expect(input).toHaveAttribute('tabindex', '-1');
    await expect(input).toHaveAttribute('aria-hidden', 'true');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Upload markup', exact: true }).click();
    const fileChooser = await chooser;
    expect(fileChooser.isMultiple()).toBe(false);
    const png = await page.evaluate(async () => {
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#e5322d';
      ctx.fillRect(20, 20, 60, 60);
      const blob = await new Promise<Blob>((resolve) => canvas.toBlob((b) => resolve(b!), 'image/png'));
      return Array.from(new Uint8Array(await blob.arrayBuffer()));
    });
    // Fully opaque squares are fine as long as the layer keeps transparent pixels.
    await fileChooser.setFiles({ name: 'markup.png', mimeType: 'image/png', buffer: Buffer.from(png) });
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Replace with upload', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Edit markup', exact: true })).toBeVisible();
  });
});

test.describe('presentation', () => {
  test('reduced motion: the editor is usable immediately with no entrance travel', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await setupEdit(page);
    await page.getByRole('button', { name: 'Draw markup', exact: true }).click();
    await expect(editorCanvas(page)).toBeVisible();
    const early = (await editorDialog(page).boundingBox())!;
    await settle(page);
    const settled = (await editorDialog(page).boundingBox())!;
    expect(Math.abs(early.x - settled.x)).toBeLessThan(1);
    expect(Math.abs(early.y - settled.y)).toBeLessThan(1);
    expect(Math.abs(early.width - settled.width)).toBeLessThan(1);
    await dragOnImage(page, [40, 128], [216, 128]);
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();
  });

  test('Simplified Chinese copy fits the dialog and the entry panel', async ({ page }) => {
    await loadApp(page, { language: 'zh-CN' });
    const source = await makeSourcePng(page);
    await page.evaluate(
      async ({ formModule, storeModule, bytes }) => {
        const form = await import(/* @vite-ignore */ formModule);
        form.promptForm.model = 'gpt-image-2.6-flare';
        const store = await import(/* @vite-ignore */ storeModule);
        store.editSourceStore.addFiles([new File([new Uint8Array(bytes)], 'source.png', { type: 'image/png' })], () => {});
      },
      { formModule: FORM_MODULE, storeModule: STORE_MODULE, bytes: Array.from(source) }
    );
    await page.getByRole('button', { name: '绘制标注', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: /^绘制标注/ });
    await expect(dialog).toBeVisible();
    await expect(page.getByLabel('标注画布', { exact: true })).toBeVisible();
    await settle(page);
    for (const label of ['画笔', '箭头', '矩形', '椭圆', '文字', '橡皮', '平移', '撤销', '重做', '清空', '应用标注', '取消']) {
      await expect(dialog.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    await expect(dialog.getByText('标注用于引导修改，不会合并到原图。')).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('zh-CN-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await settle(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath('zh-CN-mobile.png') });
  });

  for (const scheme of ['light', 'dark'] as const) {
    test(`the entry controls read clearly in the ${scheme} theme`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await setupEdit(page);
      await openEditorAndClose(page);
      const panel = page.getByRole('button', { name: 'Edit markup', exact: true }).locator('xpath=ancestor::div[contains(@class,"space-y-2")][1]');
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath(`entry-${scheme}.png`), fullPage: true });
      await expect(page.getByRole('button', { name: 'Edit markup', exact: true })).toBeVisible();
    });
  }
});

async function openEditorAndClose(page: Page) {
  await openEditor(page);
  await dragOnImage(page, [40, 128], [216, 128]);
  await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
  await expect(editorDialog(page)).toHaveCount(0);
}

test.describe('large images', () => {
  test('a 4096 x 4096 primary opens, draws a long stroke, applies and exports at full size', async ({ page }) => {
    test.setTimeout(150_000);
    await loadApp(page);
    await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption(MODEL);
    const source = await makeSourcePng(page, 4096, 4096);
    await page.getByLabel('Upload edit image').setInputFiles({ name: 'huge.png', mimeType: 'image/png', buffer: source });
    const started = Date.now();
    await page.getByRole('button', { name: 'Draw markup', exact: true }).click();
    await expect(editorCanvas(page)).toBeVisible({ timeout: 60_000 });
    await expect(editorDialog(page).getByRole('status')).toHaveCount(0, { timeout: 60_000 });
    const openMs = Date.now() - started;
    await settle(page);
    const a = await imageToScreen(page, 300, 300, 4096);
    await page.mouse.move(a.x, a.y);
    await page.mouse.down();
    for (let i = 1; i <= 60; i += 1) {
      const p = await imageToScreen(page, 300 + i * 55, 300 + Math.sin(i / 5) * 400 + i * 40, 4096);
      await page.mouse.move(p.x, p.y);
    }
    await page.mouse.up();
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0, { timeout: 60_000 });
    const totalMs = Date.now() - started;
    await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('Follow the annotation');
    const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/edits' && r.method() === 'POST');
    await page.getByRole('button', { name: 'Edits', exact: true }).click();
    const markup = multipartPart((await submitted).postDataBuffer()!, 'markup')!;
    const px = await pixelsOf(page, markup, [[10, 10]]);
    expect(px.width).toBe(4096);
    expect(px.height).toBe(4096);
    expect(px.rgba[0][3]).toBe(0);
    expect(markup.length).toBeLessThan(5 * 1024 * 1024);
    console.log(`4096x4096: open ${openMs} ms, open+draw+apply ${totalMs} ms, markup PNG ${markup.length} bytes`);
    // Reopening and cancelling a few times keeps working with the applied state.
    for (let i = 0; i < 3; i += 1) {
      await openEditor(page, 'Edit markup');
      await editorDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect(editorDialog(page)).toHaveCount(0);
    }
  });
});
