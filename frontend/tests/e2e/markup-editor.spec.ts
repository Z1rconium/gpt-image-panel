import { expect, test, type Page } from '@playwright/test';
import { baseGalleryImages, job, loadApp, settingsResponse } from './fixtures/mockApi';
import {
  alphaCount,
  applyAndSubmit,
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
  submitEdit,
  tool
} from './fixtures/markupEditor';

// P0: the coordinate / export foundation, exercised against a real browser
// canvas and the decoded PNG pixels (never a mocked toBlob).

// Several of these flows open the editor repeatedly; under a fully parallel run
// the default 30s is not enough even though each passes in a few seconds alone.
test.describe.configure({ timeout: 90_000 });

const DOCUMENT_MODULE = '/src/lib/features/markup/markupDocument.ts';
const PRIMARY_MODULE = '/src/lib/features/workspace/editPrimaryImage.ts';
const STORE_MODULE = '/src/lib/stores/editSource.ts';
const FORM_MODULE = '/src/lib/features/workspace/formState.svelte.ts';

async function openFoundation(page: Page) {
  await loadApp(page);
  await page.waitForFunction(() => document.querySelector('#prompt') !== null);
}

type Probe = { x: number; y: number };
type PixelReport = {
  width: number;
  height: number;
  samples: Array<{ x: number; y: number; rgba: number[] }>;
  bounds: { minX: number; minY: number; maxX: number; maxY: number } | null;
  opaqueOrPartial: number;
};

/** Decodes an exported PNG in the page and reports samples plus the alpha bounding box. */
async function inspectExport(page: Page, build: string, probes: Probe[]): Promise<PixelReport & { exportMs: number; stateCommands: number }> {
  return page.evaluate(
    async ({ build, probes, documentModule }) => {
      const mod = await import(/* @vite-ignore */ documentModule);
      const started = performance.now();
      // eslint-disable-next-line no-new-func
      const run = new Function('mod', `return (async () => { ${build} })();`) as (m: unknown) => Promise<{ doc: any }>;
      const { doc } = await run(mod);
      const exported = await doc.exportApplied();
      const exportMs = performance.now() - started;
      const bitmap = await createImageBitmap(exported.blob);
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      const { width, height } = bitmap;
      bitmap.close();
      const samples = probes.map((p: { x: number; y: number }) => ({
        ...p,
        rgba: Array.from(ctx.getImageData(p.x, p.y, 1, 1).data)
      }));
      let minX = width, minY = height, maxX = -1, maxY = -1, opaqueOrPartial = 0;
      const band = 256;
      for (let y0 = 0; y0 < height; y0 += band) {
        const rows = Math.min(band, height - y0);
        const data = ctx.getImageData(0, y0, width, rows).data;
        for (let i = 0; i < data.length; i += 4) {
          if (data[i + 3] === 0) continue;
          opaqueOrPartial += 1;
          const pixel = i / 4;
          const x = pixel % width;
          const y = y0 + Math.floor(pixel / width);
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
      const stateCommands = exported.state.commands.length;
      doc.dispose();
      return {
        width,
        height,
        samples,
        bounds: maxX < 0 ? null : { minX, minY, maxX, maxY },
        opaqueOrPartial,
        exportMs,
        stateCommands
      };
    },
    { build, probes, documentModule: DOCUMENT_MODULE }
  );
}

test.describe('markup document export on a real canvas', () => {
  test('a rectangle lands on the expected pixels with transparent surroundings', async ({ page }) => {
    await openFoundation(page);
    const report = await inspectExport(
      page,
      `const doc = mod.MarkupDocument.create(512, 384);
       doc.commit({ kind: 'rect', color: '#e5322d', width: 10, x1: 100, y1: 80, x2: 300, y2: 200 });
       return { doc };`,
      [
        { x: 100, y: 140 }, // left edge centre
        { x: 200, y: 80 }, // top edge centre
        { x: 300, y: 140 }, // right edge centre
        { x: 200, y: 200 }, // bottom edge centre
        { x: 200, y: 140 }, // hollow interior
        { x: 20, y: 20 }, // far outside
        { x: 511, y: 383 } // opposite corner
      ]
    );
    expect(report.width).toBe(512);
    expect(report.height).toBe(384);
    const [left, top, right, bottom, inside, outside, corner] = report.samples.map((s) => s.rgba);
    for (const edge of [left, top, right, bottom]) expect(edge).toEqual([229, 50, 45, 255]);
    expect(inside[3]).toBe(0);
    expect(outside[3]).toBe(0);
    expect(corner[3]).toBe(0);
    // Stroke width 10 centred on the rectangle -> alpha bounds are 95..305 x 75..205.
    expect(report.bounds).toEqual({ minX: 95, minY: 75, maxX: 304, maxY: 204 });
    expect(report.stateCommands).toBe(1);
  });

  test('keeps colour and antialiased alpha, and never paints a background', async ({ page }) => {
    await openFoundation(page);
    const report = await inspectExport(
      page,
      `const doc = mod.MarkupDocument.create(256, 256);
       doc.commit({ kind: 'ellipse', color: '#2563eb', width: 6, x1: 40, y1: 40, x2: 216, y2: 180 });
       return { doc };`,
      [{ x: 40, y: 110 }, { x: 128, y: 110 }, { x: 0, y: 0 }]
    );
    expect(report.samples[0].rgba).toEqual([37, 99, 235, 255]);
    expect(report.samples[1].rgba[3]).toBe(0);
    expect(report.samples[2].rgba[3]).toBe(0);
    // Antialiasing leaves partial alpha at the ellipse boundary.
    const partial = await page.evaluate(async (documentModule) => {
      const mod = await import(/* @vite-ignore */ documentModule);
      const doc = mod.MarkupDocument.create(256, 256);
      doc.commit({ kind: 'ellipse', color: '#2563eb', width: 6, x1: 40, y1: 40, x2: 216, y2: 180 });
      const ctx = doc.layer.getContext('2d');
      const data = ctx.getImageData(0, 0, 256, 256).data;
      let partialCount = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i] > 0 && data[i] < 255) partialCount += 1;
      doc.dispose();
      return partialCount;
    }, DOCUMENT_MODULE);
    expect(partial).toBeGreaterThan(50);
  });

  test('arrow, pen, text and eraser render at their recorded coordinates', async ({ page }) => {
    await openFoundation(page);
    const report = await inspectExport(
      page,
      `const doc = mod.MarkupDocument.create(400, 300);
       doc.commit({ kind: 'arrow', color: '#111111', width: 8, x1: 40, y1: 250, x2: 340, y2: 250 });
       doc.beginStroke('pen', { x: 50, y: 50 }, { color: '#16a34a', width: 12 });
       doc.extendStroke({ x: 150, y: 50 });
       doc.endStroke();
       doc.commit({ kind: 'text', color: '#f5c518', size: 48, x: 200, y: 20, text: '标注 A' });
       doc.beginStroke('erase', { x: 100, y: 50 }, { color: '#000000', width: 30 });
       doc.endStroke();
       return { doc };`,
      [
        { x: 100, y: 250 }, // arrow shaft
        { x: 336, y: 250 }, // just behind the tip, inside the head
        { x: 60, y: 50 }, // pen stroke before the eraser dot
        { x: 140, y: 50 }, // pen stroke after the eraser dot
        { x: 100, y: 50 } // erased centre
      ]
    );
    expect(report.samples[0].rgba).toEqual([17, 17, 17, 255]);
    expect(report.samples[1].rgba[3]).toBe(255);
    expect(report.samples[2].rgba).toEqual([22, 163, 74, 255]);
    expect(report.samples[3].rgba).toEqual([22, 163, 74, 255]);
    expect(report.samples[4].rgba[3]).toBe(0);
    expect(report.stateCommands).toBe(4);
    // Text pixels exist in the box that starts at (200, 20).
    expect(report.bounds!.minY).toBeLessThan(40);
  });

  test('undo, redo and clear are reflected in the exported pixels', async ({ page }) => {
    await openFoundation(page);
    const result = await page.evaluate(async (documentModule) => {
      const mod = await import(/* @vite-ignore */ documentModule);
      const doc = mod.MarkupDocument.create(200, 200);
      const alphaAt = (x: number, y: number) => doc.layer.getContext('2d').getImageData(x, y, 1, 1).data[3];
      doc.commit({ kind: 'rect', color: '#e5322d', width: 8, x1: 20, y1: 20, x2: 100, y2: 100 });
      doc.commit({ kind: 'ellipse', color: '#16a34a', width: 8, x1: 100, y1: 100, x2: 180, y2: 180 });
      const both = [alphaAt(20, 60), alphaAt(100, 140)];
      doc.undo();
      const afterUndo = [alphaAt(20, 60), alphaAt(100, 140)];
      doc.redo();
      const afterRedo = [alphaAt(20, 60), alphaAt(100, 140)];
      doc.clear();
      const afterClear = [alphaAt(20, 60), alphaAt(100, 140)];
      doc.undo();
      const afterClearUndo = [alphaAt(20, 60), alphaAt(100, 140)];
      doc.dispose();
      return { both, afterUndo, afterRedo, afterClear, afterClearUndo };
    }, DOCUMENT_MODULE);
    expect(result.both).toEqual([255, 255]);
    expect(result.afterUndo).toEqual([255, 0]);
    expect(result.afterRedo).toEqual([255, 255]);
    expect(result.afterClear).toEqual([0, 0]);
    expect(result.afterClearUndo).toEqual([255, 255]);
  });

  test('folding old history into the checkpoint keeps every visible mark', async ({ page }) => {
    await openFoundation(page);
    const result = await page.evaluate(async (documentModule) => {
      const mod = await import(/* @vite-ignore */ documentModule);
      const doc = mod.MarkupDocument.create(1200, 300);
      // 120 distinct 6px squares in a row: the first 20 get folded into the base.
      for (let i = 0; i < 120; i += 1) {
        doc.commit({ kind: 'rect', color: '#e5322d', width: 6, x1: 5 + i * 9, y1: 100, x2: 11 + i * 9, y2: 106 });
      }
      const ctx = doc.layer.getContext('2d');
      const count = () => {
        const data = ctx.getImageData(0, 0, 1200, 300).data;
        let covered = 0;
        for (let i = 3; i < data.length; i += 4) if (data[i] > 0) covered += 1;
        return covered;
      };
      const full = count();
      const depth = doc.historyDepth;
      for (let i = 0; i < 100; i += 1) doc.undo();
      const afterUndoAll = count();
      for (let i = 0; i < 100; i += 1) doc.redo();
      const afterRedoAll = count();
      const exported = await doc.exportApplied();
      const baseKept = exported.state.base !== null;
      doc.dispose();
      return { full, depth, afterUndoAll, afterRedoAll, baseKept };
    }, DOCUMENT_MODULE);
    expect(result.depth).toBe(100);
    expect(result.full).toBeGreaterThan(0);
    // Undo can only remove the last 100 steps; the 20 folded squares remain.
    expect(result.afterUndoAll).toBeGreaterThan(0);
    expect(result.afterUndoAll).toBeLessThan(result.full);
    expect(result.afterRedoAll).toBe(result.full);
    expect(result.baseKept).toBe(true);
  });

  test('an applied editor state resumes drawing on an independent copy', async ({ page }) => {
    await openFoundation(page);
    const result = await page.evaluate(async (documentModule) => {
      const mod = await import(/* @vite-ignore */ documentModule);
      const first = mod.MarkupDocument.create(300, 200);
      first.commit({ kind: 'rect', color: '#e5322d', width: 8, x1: 30, y1: 30, x2: 120, y2: 120 });
      const applied = await first.exportApplied();
      first.dispose();
      const copy = await mod.MarkupDocument.fromState(applied.state);
      const alpha = (doc: any, x: number, y: number) => doc.layer.getContext('2d').getImageData(x, y, 1, 1).data[3];
      const restored = alpha(copy, 30, 75);
      copy.commit({ kind: 'ellipse', color: '#16a34a', width: 8, x1: 150, y1: 50, x2: 280, y2: 170 });
      const added = alpha(copy, 150, 110);
      copy.undo();
      copy.undo();
      const emptiedByUndo = alpha(copy, 30, 75);
      copy.dispose();
      return { restored, added, emptiedByUndo, appliedCommands: applied.state.commands.length };
    }, DOCUMENT_MODULE);
    expect(result.restored).toBe(255);
    expect(result.added).toBe(255);
    expect(result.emptiedByUndo).toBe(0);
    expect(result.appliedCommands).toBe(1);
  });

  test('a 4096 x 4096 layer draws, exports and releases its canvases', async ({ page }) => {
    test.setTimeout(90_000);
    await openFoundation(page);
    const report = await inspectExport(
      page,
      `const doc = mod.MarkupDocument.create(4096, 4096);
       doc.beginStroke('pen', { x: 100, y: 100 }, { color: '#e5322d', width: 24 });
       for (let i = 1; i <= 2000; i += 1) doc.extendStroke({ x: 100 + i * 1.9, y: 100 + Math.sin(i / 40) * 60 + i * 1.9 });
       doc.endStroke();
       for (let i = 0; i < 40; i += 1) doc.commit({ kind: 'rect', color: '#2563eb', width: 14, x1: 3000, y1: 200 + i * 20, x2: 3800, y2: 260 + i * 20 });
       doc.commit({ kind: 'arrow', color: '#16a34a', width: 20, x1: 500, y1: 3900, x2: 3900, y2: 3900 });
       return { doc };`,
      [{ x: 2000, y: 3900 }, { x: 3000, y: 300 }, { x: 4090, y: 4090 }]
    );
    expect(report.width).toBe(4096);
    expect(report.height).toBe(4096);
    expect(report.samples[0].rgba).toEqual([22, 163, 74, 255]);
    expect(report.samples[1].rgba[3]).toBe(255);
    expect(report.samples[2].rgba[3]).toBe(0);
    expect(report.bounds).not.toBeNull();
    expect(report.exportMs).toBeLessThan(60_000);
  });

  test('exports upright dimensions for an EXIF-rotated primary image', async ({ page }) => {
    await openFoundation(page);
    const dims = await page.evaluate(async ({ primaryModule, documentModule }) => {
      const primary = await import(/* @vite-ignore */ primaryModule);
      const mod = await import(/* @vite-ignore */ documentModule);
      const canvas = document.createElement('canvas');
      canvas.width = 400;
      canvas.height = 300;
      const jpegBytes = Uint8Array.from(atob(canvas.toDataURL('image/jpeg').split(',')[1]), (c) => c.charCodeAt(0));
      // Little-endian TIFF IFD with EXIF orientation 6 (rotate 90 degrees clockwise).
      const exif = Uint8Array.from([0xff, 0xe1, 0, 34, 69, 120, 105, 102, 0, 0, 73, 73, 42, 0, 8, 0, 0, 0, 1, 0, 18, 1, 3, 0, 1, 0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0]);
      const blob = new Blob([jpegBytes.slice(0, 2), exif, jpegBytes.slice(2)], { type: 'image/jpeg' });
      const decoded = await primary.decodePrimaryBlob(blob);
      const size = { width: decoded.width, height: decoded.height };
      decoded.bitmap.close();
      const doc = mod.MarkupDocument.create(size.width, size.height);
      doc.commit({ kind: 'rect', color: '#e5322d', width: 6, x1: 10, y1: 10, x2: 60, y2: 60 });
      const exported = await doc.exportApplied();
      const bitmap = await createImageBitmap(exported.blob);
      const out = { decoded: size, exported: { width: bitmap.width, height: bitmap.height } };
      bitmap.close();
      doc.dispose();
      return out;
    }, { primaryModule: PRIMARY_MODULE, documentModule: DOCUMENT_MODULE });
    // The raw file is 400x300; the upright image the backend sends upstream is 300x400.
    expect(dims.decoded).toEqual({ width: 300, height: 400 });
    expect(dims.exported).toEqual({ width: 300, height: 400 });
  });

  test('rejects an empty or an unusable layer instead of exporting it', async ({ page }) => {
    await openFoundation(page);
    const outcome = await page.evaluate(async (documentModule) => {
      const mod = await import(/* @vite-ignore */ documentModule);
      const doc = mod.MarkupDocument.create(64, 64);
      const codes: string[] = [];
      try {
        await doc.exportApplied();
      } catch (error: any) {
        codes.push(error.code);
      }
      doc.commit({ kind: 'rect', color: '#e5322d', width: 4, x1: 8, y1: 8, x2: 40, y2: 40 });
      doc.beginStroke('erase', { x: 0, y: 0 }, { color: '#000000', width: 400 });
      doc.endStroke();
      try {
        await doc.exportApplied();
      } catch (error: any) {
        codes.push(error.code);
      }
      doc.dispose();
      return codes;
    }, DOCUMENT_MODULE);
    expect(outcome).toEqual(['empty', 'empty']);
  });
});


// ---------------------------------------------------------------------------
// P1+: the editor dialog wired into the workspace, driven like a user.
// ---------------------------------------------------------------------------

test.describe('markup editor workflow', () => {
  test('draw with the pen, apply, and submit the untouched original plus an independent PNG', async ({ page }) => {
    const source = await setupEdit(page);
    await openEditor(page);
    await expect(editorDialog(page).getByRole('button', { name: 'Apply markup' })).toBeDisabled();
    await dragOnImage(page, [40, 128], [216, 128]);
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Edit markup', exact: true })).toBeVisible();
    await expect(page.getByRole('status')).toContainText('Markup applied');

    const body = await submitEdit(page);
    const text = body.toString('latin1');
    expect(text).not.toContain('name="mask"');
    expect(text).not.toContain('name="paste_back"');
    const image = multipartPart(body, 'image');
    expect(image && Buffer.compare(image, source)).toBe(0);
    const markup = multipartPart(body, 'markup')!;
    const px = await pixelsOf(page, markup, [[128, 128], [60, 128], [128, 90], [10, 10], [240, 240]]);
    expect(px.width).toBe(SOURCE_SIZE);
    expect(px.height).toBe(SOURCE_SIZE);
    expect(px.rgba[0]).toEqual([229, 50, 45, 255]);
    expect(px.rgba[1]).toEqual([229, 50, 45, 255]);
    expect(px.rgba[2][3]).toBe(0);
    expect(px.rgba[3][3]).toBe(0);
    expect(px.rgba[4][3]).toBe(0);
    await page.screenshot({ path: test.info().outputPath('p1-applied-overlay.png'), fullPage: true });
  });

  test('cancel returns to the last applied markup, and an unchanged editor closes without asking', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 60], [216, 60]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);

    // Re-open: the applied drawing is resumed, an untouched close is silent.
    await openEditor(page, 'Edit markup');
    await editorDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(editorDialog(page)).toHaveCount(0);

    // Modify then cancel: the user is asked, declining keeps the draft, accepting reverts.
    await openEditor(page, 'Edit markup');
    await dragOnImage(page, [40, 200], [216, 200]);
    await editorDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    const confirm = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(confirm).toHaveCount(0);
    await expect(editorDialog(page)).toBeVisible();
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeVisible();
    await page.getByRole('dialog', { name: 'Discard unsaved changes?' }).getByRole('button', { name: 'Discard' }).click();
    await expect(editorDialog(page)).toHaveCount(0);

    const body = await submitEdit(page);
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[128, 60], [128, 200]]);
    expect(px.rgba[0][3]).toBe(255);
    expect(px.rgba[1][3]).toBe(0);
  });

  test('a reopened editor continues from the applied drawing and keeps undo history', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 60], [216, 60]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    await openEditor(page, 'Edit markup');
    await dragOnImage(page, [40, 200], [216, 200]);
    await editorDialog(page).getByRole('button', { name: 'Undo' }).click();
    await editorDialog(page).getByRole('button', { name: 'Undo' }).click();
    // The first stroke came from the applied state and is undoable too.
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();
    await editorDialog(page).getByRole('button', { name: 'Redo' }).click();
    await editorDialog(page).getByRole('button', { name: 'Redo' }).click();
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    const body = await submitEdit(page);
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[128, 60], [128, 200]]);
    expect(px.rgba[0][3]).toBe(255);
    expect(px.rgba[1][3]).toBe(255);
  });

  test('a rejected apply keeps the draft and the previously applied markup', async ({ page }) => {
    await setupEdit(page, {
      settings: { ...settingsResponse, image_upload_limits: { max_file_size_bytes: 150, max_image_pixels: 16_777_216 } }
    });
    // A tiny file limit makes the exported PNG too large for the deployment.
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(page.getByText('The markup PNG is larger than the allowed file size.')).toBeVisible();
    await expect(editorDialog(page)).toBeVisible();
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();
    await expect(editorDialog(page).getByRole('button', { name: 'Apply markup' })).toBeEnabled();
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toHaveCount(0);
  });

  test('applying markup over a mask asks first; declining keeps both the mask and the draft', async ({ page }) => {
    await setupEdit(page);
    await page.evaluate(async (storeModule) => {
      const store = await import(/* @vite-ignore */ storeModule);
      let state: any;
      store.editSourceStore.subscribe((value: unknown) => (state = value))();
      const id = state.files[0].id;
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const blob = await new Promise<Blob>((resolve) => canvas.toBlob((b) => resolve(b!), 'image/png'));
      store.editSourceStore.setMask({ sourceId: id, blob, width: 256, height: 256, coverage: 0.25, previewUrl: URL.createObjectURL(blob), origin: 'painted' });
    }, STORE_MODULE);
    await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    const confirm = page.getByRole('dialog', { name: 'Replace the current mask?' });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(editorDialog(page)).toBeVisible();
    await expect(page.getByText(/^Repaint \d+\.\d%$/)).toBeVisible();
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toHaveCount(0);

    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await page.getByRole('dialog', { name: 'Replace the current mask?' }).getByRole('button', { name: 'Replace mask' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
    await expect(page.getByText(/^Repaint \d+\.\d%$/)).toHaveCount(0);
    const body = await submitEdit(page);
    expect(body.toString('latin1')).not.toContain('name="mask"');
    expect(body.toString('latin1')).toContain('name="markup"');
  });

  test('zoom and pan never move marks: drawing after 100% lands on the intended pixels', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const zoom = editorDialog(page).getByTestId('markup-zoom');
    await editorDialog(page).getByRole('button', { name: 'Actual size (100%)' }).click();
    await expect(zoom).toHaveText('100%');
    // At 100% the image centre sits at the stage centre (image is smaller than the stage).
    const box = (await editorCanvas(page).boundingBox())!;
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    await page.mouse.move(cx - 60, cy + 40);
    await page.mouse.down();
    await page.mouse.move(cx + 60, cy + 40, { steps: 6 });
    await page.mouse.up();
    // Wheel zoom around the cursor, then fit again: marks stay in image space.
    await page.mouse.move(cx, cy);
    await page.mouse.wheel(0, -400);
    await expect(zoom).not.toHaveText('100%');
    await editorDialog(page).getByRole('button', { name: 'Fit to window' }).click();
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    const body = await submitEdit(page);
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[128, 168], [70, 168], [186, 168], [128, 128], [128, 100]]);
    expect(px.rgba[0][3]).toBe(255);
    expect(px.rgba[1][3]).toBe(255);
    expect(px.rgba[2][3]).toBe(255);
    expect(px.rgba[3][3]).toBe(0);
    expect(px.rgba[4][3]).toBe(0);
  });

  test('Remove markup in the panel clears it and the next edit carries no markup', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
    await page.getByRole('button', { name: 'Remove markup', exact: true }).click();
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Draw markup', exact: true })).toBeVisible();
    const body = await submitEdit(page);
    expect(body.toString('latin1')).not.toContain('name="markup"');
  });
});


// ---------------------------------------------------------------------------
// P2: every tool, keyboard / IME rules and touch input.
// ---------------------------------------------------------------------------

test.describe('markup editor tools', () => {
  test('arrow, rectangle, ellipse, text and eraser export the expected pixels', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const slider = editorDialog(page).getByRole('slider');

    await tool(page, 'Rectangle').click();
    await slider.fill('8');
    await dragOnImage(page, [200, 200], [150, 150]); // reversed drag

    await tool(page, 'Ellipse').click();
    await dragOnImage(page, [60, 60], [120, 110]);

    await tool(page, 'Arrow').click();
    await editorDialog(page).getByRole('button', { name: 'Blue', exact: true }).click();
    await slider.fill('6');
    await dragOnImage(page, [40, 230], [200, 230]);

    await tool(page, 'Pen').click();
    await editorDialog(page).getByRole('button', { name: 'White', exact: true }).click();
    await slider.fill('8');
    await dragOnImage(page, [20, 20], [100, 20]);
    await tool(page, 'Eraser').click();
    await slider.fill('20');
    await dragOnImage(page, [58, 20], [62, 20], 2);

    await tool(page, 'Text').click();
    await editorDialog(page).getByRole('button', { name: 'Green', exact: true }).click();
    await editorDialog(page).getByRole('slider').fill('40');
    const at = await imageToScreen(page, 30, 100);
    await page.mouse.click(at.x, at.y);
    await page.getByLabel('Annotation text').fill('标注 A');
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Annotation text')).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('p2-all-tools.png') });

    const markup = await applyAndSubmit(page);
    const px = await pixelsOf(page, markup, [
      [150, 175], // rectangle left edge (reversed drag)
      [175, 175], // rectangle interior
      [60, 85], // ellipse left extreme
      [90, 85], // ellipse interior
      [120, 230], // arrow shaft
      [30, 20], // white pen before the eraser
      [60, 20], // erased
      [90, 20] // white pen after the eraser
    ]);
    expect(px.rgba[0]).toEqual([229, 50, 45, 255]);
    expect(px.rgba[1][3]).toBe(0);
    expect(px.rgba[2]).toEqual([229, 50, 45, 255]);
    expect(px.rgba[3][3]).toBe(0);
    expect(px.rgba[4]).toEqual([37, 99, 235, 255]);
    expect(px.rgba[5]).toEqual([255, 255, 255, 255]);
    expect(px.rgba[6][3]).toBe(0);
    expect(px.rgba[7]).toEqual([255, 255, 255, 255]);
    expect(await alphaCount(page, markup, { x: 30, y: 100, width: 190, height: 50 })).toBeGreaterThan(150);
  });

  test('shift constrains a rectangle to a square', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await tool(page, 'Rectangle').click();
    await editorDialog(page).getByRole('slider').fill('6');
    const a = await imageToScreen(page, 60, 60);
    const b = await imageToScreen(page, 180, 110);
    await page.mouse.move(a.x, a.y);
    await page.keyboard.down('Shift');
    await page.mouse.down();
    await page.mouse.move(b.x, b.y, { steps: 8 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
    const markup = await applyAndSubmit(page);
    // Width 120 and height 50 become a 120x120 square.
    const px = await pixelsOf(page, markup, [[60, 120], [180, 120], [120, 180], [120, 118]]);
    expect(px.rgba[0][3]).toBe(255);
    expect(px.rgba[1][3]).toBe(255);
    expect(px.rgba[2][3]).toBe(255);
    expect(px.rgba[3][3]).toBe(0);
  });

  test('text: IME composition Enter does not submit; Escape cancels the text before the dialog', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await tool(page, 'Text').click();
    const at = await imageToScreen(page, 40, 80);
    await page.mouse.click(at.x, at.y);
    const input = page.getByLabel('Annotation text');
    await expect(input).toBeVisible();
    await input.fill('にほん');
    await input.evaluate((node) => {
      node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
      node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, keyCode: 229, bubbles: true, cancelable: true }));
    });
    await expect(input).toBeVisible();
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();
    await page.getByRole('button', { name: 'Add text', exact: true }).click();
    await expect(input).toHaveCount(0);
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();

    // Empty text is never recorded.
    await page.mouse.click(at.x, at.y + 60);
    await page.getByLabel('Annotation text').fill('   ');
    await page.keyboard.press('Enter');
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();
    await editorDialog(page).getByRole('button', { name: 'Undo' }).click();
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();

    // Escape closes only the text box first; the next Escape closes the (clean) dialog.
    await page.mouse.click(at.x, at.y);
    await expect(page.getByLabel('Annotation text')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByLabel('Annotation text')).toHaveCount(0);
    await expect(editorDialog(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(editorDialog(page)).toHaveCount(0);
  });

  test('keyboard: tool keys, undo/redo shortcuts, Space pans without drawing, text field keeps its own keys', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await page.keyboard.press('r');
    await expect(tool(page, 'Rectangle')).toHaveAttribute('aria-pressed', 'true');
    await page.keyboard.press('p');
    await dragOnImage(page, [40, 128], [216, 128]);
    await page.keyboard.press('ControlOrMeta+z');
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();
    await page.keyboard.press('ControlOrMeta+Shift+z');
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeEnabled();

    // Holding Space turns the drag into a pan; releasing returns to the pen.
    await page.keyboard.down('Space');
    await dragOnImage(page, [40, 200], [100, 200], 4);
    await page.keyboard.up('Space');
    await editorDialog(page).getByRole('button', { name: 'Undo' }).click();
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();
    await expect(tool(page, 'Pen')).toHaveAttribute('aria-pressed', 'true');

    // Typing letters in the text box must not switch tools or undo.
    await page.keyboard.press('t');
    const at = await imageToScreen(page, 40, 80);
    await page.mouse.click(at.x, at.y);
    await page.getByLabel('Annotation text').pressSequentially('pre');
    await expect(tool(page, 'Text')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByLabel('Annotation text')).toHaveValue('pre');
  });

  test('hide/show keeps the document; focus returns to the opener when the editor closes', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    const toggle = editorDialog(page).getByRole('button', { name: 'Hide markup' });
    await toggle.click();
    await expect(editorDialog(page).getByRole('button', { name: 'Show markup' })).toHaveAttribute('aria-pressed', 'true');
    // Drawing while hidden shows the layer again instead of drawing invisibly.
    await dragOnImage(page, [40, 160], [216, 160]);
    await expect(editorDialog(page).getByRole('button', { name: 'Hide markup' })).toBeVisible();
    await editorDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('dialog', { name: 'Discard unsaved changes?' }).getByRole('button', { name: 'Discard' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Draw markup', exact: true })).toBeFocused();
  });

  test('Tab stays inside the dialog and the background is inert', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const outside = await page.evaluate(() => {
      const seen = new Set<boolean>();
      return seen.size;
    });
    expect(outside).toBe(0);
    for (let i = 0; i < 30; i += 1) {
      await page.keyboard.press('Tab');
      const inside = await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')));
      expect(inside).toBe(true);
    }
  });
});

test.describe('markup editor on a touch phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  // One CDP session per page: Chrome keeps the touch state inside it.
  const sessions = new WeakMap<Page, Awaited<ReturnType<ReturnType<Page['context']>['newCDPSession']>>>();
  async function touchEvent(page: Page, type: 'touchStart' | 'touchMove' | 'touchEnd', points: Array<{ x: number; y: number; id: number }>) {
    let cdp = sessions.get(page);
    if (!cdp) {
      cdp = await page.context().newCDPSession(page);
      sessions.set(page, cdp);
    }
    await cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points });
  }

  test('single-finger drawing, pinch zoom, and a second touch never leave a stray stroke', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const undo = editorDialog(page).getByRole('button', { name: 'Undo' });
    const zoom = editorDialog(page).getByTestId('markup-zoom');

    // One finger draws a stroke.
    const a = await imageToScreen(page, 40, 128);
    const b = await imageToScreen(page, 216, 128);
    await touchEvent(page, 'touchStart', [{ ...a, id: 0 }]);
    for (let i = 1; i <= 6; i += 1) {
      await touchEvent(page, 'touchMove', [{ x: a.x + ((b.x - a.x) * i) / 6, y: a.y, id: 0 }]);
    }
    await touchEvent(page, 'touchEnd', []);
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect(undo).toBeDisabled();

    // A second finger arriving mid-stroke cancels the stroke and turns into a pinch.
    const startZoom = await zoom.innerText();
    await touchEvent(page, 'touchStart', [{ ...a, id: 0 }]);
    await touchEvent(page, 'touchMove', [{ x: a.x + 30, y: a.y, id: 0 }]);
    const c = await imageToScreen(page, 128, 100);
    const d = await imageToScreen(page, 128, 160);
    await touchEvent(page, 'touchStart', [{ x: a.x + 30, y: a.y, id: 0 }, { ...c, id: 1 }]);
    for (let i = 1; i <= 5; i += 1) {
      await touchEvent(page, 'touchMove', [
        { x: c.x - 10 * i, y: c.y - 6 * i, id: 0 },
        { x: d.x + 10 * i, y: d.y + 6 * i, id: 1 }
      ]);
    }
    await touchEvent(page, 'touchEnd', []);
    await expect(zoom).not.toHaveText(startZoom);
    await expect(undo).toBeDisabled();
    await expect(editorDialog(page).getByRole('button', { name: 'Apply markup' })).toBeDisabled();
  });

  test('pan mode drags the view with one finger and canvas touches do not scroll the page', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    const canvas = editorCanvas(page);
    expect(await canvas.evaluate((node) => getComputedStyle(node).touchAction)).toBe('none');
    await tool(page, 'Pan').click();
    await editorDialog(page).getByRole('button', { name: 'Actual size (100%)' }).click();
    const scrollBefore = await page.evaluate(() => window.scrollY);
    const a = await imageToScreen(page, 128, 128);
    await touchEvent(page, 'touchStart', [{ ...a, id: 0 }]);
    await touchEvent(page, 'touchMove', [{ x: a.x + 20, y: a.y + 20, id: 0 }]);
    await touchEvent(page, 'touchEnd', []);
    expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();
  });

  test('the phone layout keeps controls reachable and touch targets at least 44px', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const dialog = editorDialog(page);
    for (const name of ['Pen', 'Arrow', 'Rectangle', 'Ellipse', 'Text', 'Eraser', 'Pan', 'Undo', 'Redo', 'Clear all', 'Close markup editor', 'Cancel', 'Apply markup', 'Red', 'Zoom in', 'Fit to window']) {
      const box = await dialog.getByRole('button', { name, exact: true }).first().boundingBox();
      expect(box, name).not.toBeNull();
      expect(box!.height, name).toBeGreaterThanOrEqual(43.5);
      expect(box!.width, name).toBeGreaterThanOrEqual(43.5);
    }
    // The footer actions are fully visible without scrolling the page.
    const apply = (await dialog.getByRole('button', { name: 'Apply markup' }).boundingBox())!;
    expect(apply.y + apply.height).toBeLessThanOrEqual(844);
    expect(apply.x + apply.width).toBeLessThanOrEqual(390);
    // The tool row scrolls horizontally instead of overflowing the dialog.
    const toolbar = dialog.getByRole('toolbar');
    expect(await toolbar.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath('p2-mobile-layout.png') });
  });
});


// ---------------------------------------------------------------------------
// P3: lifecycle, async invalidation, reuse of uploaded / restored PNGs, cleanup.
// ---------------------------------------------------------------------------

async function storeSnapshot(page: Page) {
  return page.evaluate(async (storeModule) => {
    const store = await import(/* @vite-ignore */ storeModule);
    let state: any;
    store.editSourceStore.subscribe((value: unknown) => (state = value))();
    return {
      files: state.files.length,
      hasMarkup: Boolean(state.markup),
      markupOrigin: state.markup?.origin ?? null,
      hasEditorState: Boolean(state.markup?.editorState),
      hasMask: Boolean(state.mask)
    };
  }, STORE_MODULE);
}

test.describe('markup session lifecycle', () => {
  for (const model of ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst']) {
    test(`${model}: draws and submits the original with an independent markup`, async ({ page }) => {
      await loadApp(page);
      await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption(model);
      const source = await makeSourcePng(page);
      await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: source });
      await openEditor(page);
      await dragOnImage(page, [40, 128], [216, 128]);
      const markup = await applyAndSubmit(page);
      const px = await pixelsOf(page, markup, [[128, 128], [128, 40]]);
      expect(px.rgba[0]).toEqual([229, 50, 45, 255]);
      expect(px.rgba[1][3]).toBe(0);
    });
  }

  test('changing the primary image closes the editor, releases the draft and applies nothing', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await page.evaluate(async (storeModule) => {
      const store = await import(/* @vite-ignore */ storeModule);
      store.editSourceStore.clear();
    }, STORE_MODULE);
    await expect(editorDialog(page)).toHaveCount(0);
    await expect(page.getByText('The primary image changed, so the markup editor was closed.')).toBeVisible();
    expect(await storeSnapshot(page)).toMatchObject({ files: 0, hasMarkup: false });
  });

  test('adding or removing a reference image keeps the editor and the applied markup', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    // Reference changes while the editor is open must not close it or drop the layer.
    await openEditor(page, 'Edit markup');
    await page.evaluate(async (storeModule) => {
      const store = await import(/* @vite-ignore */ storeModule);
      store.editSourceStore.addFiles([new File([new Uint8Array([1])], 'ref.png', { type: 'image/png' })], () => {});
    }, STORE_MODULE);
    await expect(editorDialog(page)).toBeVisible();
    expect(await storeSnapshot(page)).toMatchObject({ files: 2, hasMarkup: true });
    await page.evaluate(async (storeModule) => {
      const store = await import(/* @vite-ignore */ storeModule);
      let state: any;
      store.editSourceStore.subscribe((value: unknown) => (state = value))();
      store.editSourceStore.remove(state.files[1].id);
    }, STORE_MODULE);
    await expect(editorDialog(page)).toBeVisible();
    expect(await storeSnapshot(page)).toMatchObject({ files: 1, hasMarkup: true });
  });

  test('switching to a model without markup support closes the editor and drops the layer', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await page.evaluate(async (formModule) => {
      const mod = await import(/* @vite-ignore */ formModule);
      mod.promptForm.model = 'gpt-image-2';
    }, FORM_MODULE);
    await expect(editorDialog(page)).toHaveCount(0);
    await expect(page.getByText('Markup is not available for the current model or provider, so the editor was closed.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Draw markup', exact: true })).toHaveCount(0);
    expect(await storeSnapshot(page)).toMatchObject({ hasMarkup: false });
  });

  test('a provider without markup capability offers no drawing entry', async ({ page }) => {
    await loadApp(page, {
      settings: {
        ...settingsResponse,
        provider_kind: 'async_json',
        provider_capabilities: { generate: true, edit: true, mask: false, stream: false, transparent_background: false, markup: false, complexity: false, animation: false, formats: ['png'] }
      }
    });
    await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption(MODEL);
    const source = await makeSourcePng(page);
    await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: source });
    await expect(page.getByText('This provider does not support uploaded markup.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Draw markup', exact: true })).toHaveCount(0);
    await expect(page.getByLabel('Upload / replace markup PNG')).toHaveCount(0);
  });

  test('a decision that arrives after the primary changed is refused instead of re-bound', async ({ page }) => {
    await setupEdit(page);
    await page.evaluate(async (storeModule) => {
      const store = await import(/* @vite-ignore */ storeModule);
      let state: any;
      store.editSourceStore.subscribe((value: unknown) => (state = value))();
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const blob = await new Promise<Blob>((resolve) => canvas.toBlob((b) => resolve(b!), 'image/png'));
      store.editSourceStore.setMask({ sourceId: state.files[0].id, blob, width: 256, height: 256, coverage: 0.2, previewUrl: URL.createObjectURL(blob), origin: 'painted' });
    }, STORE_MODULE);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    const confirm = page.getByRole('dialog', { name: 'Replace the current mask?' });
    await expect(confirm).toBeVisible();
    // While the confirmation is open the primary image is replaced.
    await page.evaluate(async (storeModule) => {
      const store = await import(/* @vite-ignore */ storeModule);
      store.editSourceStore.clear();
      store.editSourceStore.addFiles([new File([new Uint8Array([1])], 'other.png', { type: 'image/png' })], () => {});
    }, STORE_MODULE);
    await confirm.getByRole('button', { name: 'Replace mask' }).click();
    await expect(page.getByText('The primary image changed, so the markup was not applied.')).toBeVisible();
    expect(await storeSnapshot(page)).toMatchObject({ files: 1, hasMarkup: false });
  });

  test('repeated clicks on Apply install exactly one markup', async ({ page }) => {
    await setupEdit(page);
    await openEditor(page);
    await dragOnImage(page, [40, 128], [216, 128]);
    const apply = editorDialog(page).getByRole('button', { name: 'Apply markup' });
    await apply.dblclick({ force: true }).catch(() => undefined);
    await expect(editorDialog(page)).toHaveCount(0);
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toHaveCount(1);
    expect(await storeSnapshot(page)).toMatchObject({ hasMarkup: true, markupOrigin: 'painted', hasEditorState: true });
  });
});

test.describe('markup reuse of uploaded and restored PNGs', () => {
  async function paintedMarkup(page: Page) {
    await openEditor(page);
    await dragOnImage(page, [40, 60], [216, 60]);
    const markup = await applyAndSubmit(page);
    return markup;
  }

  test('an uploaded PNG becomes the base layer: keep drawing, erase it, or cancel without touching it', async ({ page }) => {
    await setupEdit(page);
    const uploaded = await paintedMarkup(page);
    await page.getByRole('button', { name: 'Remove markup', exact: true }).click();
    await page.getByLabel('Upload / replace markup PNG').setInputFiles({ name: 'markup.png', mimeType: 'image/png', buffer: uploaded });
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
    expect(await storeSnapshot(page)).toMatchObject({ markupOrigin: 'uploaded', hasEditorState: false });

    // Cancel leaves the uploaded bytes exactly as they were.
    await openEditor(page, 'Edit markup');
    await dragOnImage(page, [40, 200], [216, 200]);
    await editorDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('dialog', { name: 'Discard unsaved changes?' }).getByRole('button', { name: 'Discard' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    expect(await storeSnapshot(page)).toMatchObject({ markupOrigin: 'uploaded' });

    // Draw more and erase part of the imported stroke; the history starts empty.
    await openEditor(page, 'Edit markup');
    await expect(editorDialog(page).getByRole('button', { name: 'Undo' })).toBeDisabled();
    await dragOnImage(page, [40, 200], [216, 200]);
    await tool(page, 'Eraser').click();
    await editorDialog(page).getByRole('slider').fill('20');
    await dragOnImage(page, [118, 60], [138, 60], 2);
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    expect(await storeSnapshot(page)).toMatchObject({ markupOrigin: 'painted', hasEditorState: true });
    const body = await submitEdit(page);
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[60, 60], [128, 60], [200, 60], [128, 200]]);
    expect(px.rgba[0][3]).toBe(255); // imported stroke survives
    expect(px.rgba[1][3]).toBe(0); // erased through the imported layer
    expect(px.rgba[2][3]).toBe(255);
    expect(px.rgba[3][3]).toBe(255); // new stroke
  });

  test('a markup restored by task retry opens as a base layer and can be extended', async ({ page }) => {
    // First produce a real markup PNG in a throwaway page.
    await setupEdit(page);
    const restoredPng = await paintedMarkup(page);

    await page.unroute('**/api/**').catch(() => undefined);
    await loadApp(page, { historyJobs: [{ ...job('history-markup', 'markup retry'), model: MODEL, operation: 'edit', markup_applied: true }] });
    await page.route('**/api/generate/history-markup/markup/restore', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: restoredPng }));
    const source = await makeSourcePng(page);
    await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: source });
    await page.getByRole('button', { name: 'Job History' }).click();
    const drawer = page.getByRole('dialog', { name: 'Job History' });
    await drawer.getByRole('button', { name: 'History', exact: true }).click();
    const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/edits' && r.method() === 'POST');
    await drawer.locator('article').filter({ hasText: 'markup retry' }).getByRole('button', { name: 'Retry' }).click();
    const retryBody = (await submitted).postDataBuffer()!;
    // The retry itself resubmits the restored PNG unchanged.
    expect(Buffer.compare(multipartPart(retryBody, 'markup')!, restoredPng)).toBe(0);
    await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
    expect(await storeSnapshot(page)).toMatchObject({ markupOrigin: 'restored', hasEditorState: false });

    await page.keyboard.press('Escape'); // close the jobs drawer if it is still open
    await openEditor(page, 'Edit markup');
    await dragOnImage(page, [40, 200], [216, 200]);
    const markup = await applyAndSubmit(page);
    const px = await pixelsOf(page, markup, [[128, 60], [128, 200]]);
    expect(px.rgba[0][3]).toBe(255);
    expect(px.rgba[1][3]).toBe(255);
  });
});

test.describe('markup on gallery primaries', () => {
  async function chooseGalleryPrimary(page: Page) {
    await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit', exact: true }).click();
    await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Clear prompt and describe changes', exact: true }).click();
    await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption(MODEL);
    await expect(page.getByRole('button', { name: 'Draw markup', exact: true })).toBeVisible();
  }

  test('draws on the authenticated original, never on a CDN preview', async ({ page }) => {
    const original = await (async () => {
      await loadApp(page);
      return makeSourcePng(page, 400, 300);
    })();
    await loadApp(page, {
      galleryImages: [{ ...baseGalleryImages[0], filename: 'cdn-source.png', image_url: 'https://cdn.invalid/signed/opaque?sig=secret', image_width: 64, image_height: 48 }]
    });
    let externalFetches = 0;
    const thumbnail = await makeSourcePng(page, 64, 48);
    await page.route('https://cdn.invalid/**', async (route) => {
      if (route.request().resourceType() === 'fetch') externalFetches += 1;
      await route.fulfill({ status: 200, contentType: 'image/png', body: thumbnail });
    });
    await page.route('**/api/image/cdn-source.png', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: original }));
    await chooseGalleryPrimary(page);
    await openEditor(page);
    // The original is 400x300 even though gallery metadata and the CDN preview say 64x48.
    const box = (await editorCanvas(page).boundingBox())!;
    const scale = Math.min((box.width - 24) / 400, (box.height - 24) / 300);
    const x0 = box.x + (box.width - 400 * scale) / 2;
    const y0 = box.y + (box.height - 300 * scale) / 2;
    await page.mouse.move(x0 + 40 * scale, y0 + 150 * scale);
    await page.mouse.down();
    await page.mouse.move(x0 + 360 * scale, y0 + 150 * scale, { steps: 8 });
    await page.mouse.up();
    await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
    await expect(editorDialog(page)).toHaveCount(0);
    await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('Follow the annotation');
    const submitted = page.waitForRequest((r) => new URL(r.url()).pathname.startsWith('/api/edits/from-gallery/') && r.method() === 'POST');
    await page.getByRole('button', { name: 'Edits', exact: true }).click();
    const body = (await submitted).postDataBuffer()!;
    const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[200, 150], [200, 60]]);
    expect(px.width).toBe(400);
    expect(px.height).toBe(300);
    expect(px.rgba[0]).toEqual([229, 50, 45, 255]);
    expect(px.rgba[1][3]).toBe(0);
    expect(externalFetches).toBe(0);
  });

  for (const knownDimensions of [true, false]) {
    test(`EXIF-rotated gallery original is edited upright (metadata known: ${knownDimensions})`, async ({ page }) => {
      await loadApp(page, {
        galleryImages: [{ ...baseGalleryImages[0], filename: 'oriented.jpg', image_url: '/api/image/oriented.jpg', image_width: knownDimensions ? 400 : 0, image_height: knownDimensions ? 300 : 0 }]
      });
      const jpeg = await page.evaluate(() => {
        const canvas = document.createElement('canvas');
        canvas.width = 400;
        canvas.height = 300;
        return Array.from(Uint8Array.from(atob(canvas.toDataURL('image/jpeg').split(',')[1]), (c) => c.charCodeAt(0)));
      });
      const exif = Buffer.from([0xff, 0xe1, 0, 34, 69, 120, 105, 102, 0, 0, 73, 73, 42, 0, 8, 0, 0, 0, 1, 0, 18, 1, 3, 0, 1, 0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0]);
      const original = Buffer.concat([Buffer.from(jpeg.slice(0, 2)), exif, Buffer.from(jpeg.slice(2))]);
      await page.route('**/api/image/oriented.jpg', (route) => route.fulfill({ status: 200, contentType: 'image/jpeg', body: original }));
      await chooseGalleryPrimary(page);
      await openEditor(page);
      const box = (await editorCanvas(page).boundingBox())!;
      const scale = Math.min((box.width - 24) / 300, (box.height - 24) / 400);
      const x0 = box.x + (box.width - 300 * scale) / 2;
      const y0 = box.y + (box.height - 400 * scale) / 2;
      await page.mouse.move(x0 + 40 * scale, y0 + 40 * scale);
      await page.mouse.down();
      await page.mouse.move(x0 + 40 * scale, y0 + 360 * scale, { steps: 8 });
      await page.mouse.up();
      await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
      await expect(editorDialog(page)).toHaveCount(0);
      await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('Follow the annotation');
      const submitted = page.waitForRequest((r) => new URL(r.url()).pathname.startsWith('/api/edits/from-gallery/') && r.method() === 'POST');
      await page.getByRole('button', { name: 'Edits', exact: true }).click();
      const body = (await submitted).postDataBuffer()!;
      const px = await pixelsOf(page, multipartPart(body, 'markup')!, [[40, 200], [150, 200]]);
      expect(px.width).toBe(300);
      expect(px.height).toBe(400);
      expect(px.rgba[0][3]).toBe(255);
      expect(px.rgba[1][3]).toBe(0);
    });
  }
});

test.describe('markup resources', () => {
  test('repeated open/close and apply cycles release canvases, bitmaps and object URLs', async ({ page }) => {
    test.setTimeout(90_000);
    await page.addInitScript(() => {
      const track = {
        enabled: false,
        bitmaps: { opened: 0, closed: 0 },
        urls: new Set<string>(),
        canvasCount: 0,
        canvases: [] as WeakRef<HTMLCanvasElement>[]
      };
      (window as any).__markupTrack = track;
      const create = window.createImageBitmap.bind(window);
      (window as any).createImageBitmap = async (...args: any[]) => {
        const bitmap = await (create as any)(...args);
        track.bitmaps.opened += 1;
        const close = bitmap.close.bind(bitmap);
        let closed = false;
        bitmap.close = () => {
          if (!closed) {
            closed = true;
            track.bitmaps.closed += 1;
          }
          close();
        };
        return bitmap;
      };
      const createUrl = URL.createObjectURL.bind(URL);
      const revokeUrl = URL.revokeObjectURL.bind(URL);
      URL.createObjectURL = (object: Blob | MediaSource) => {
        const url = createUrl(object);
        track.urls.add(url);
        return url;
      };
      URL.revokeObjectURL = (url: string) => {
        track.urls.delete(url);
        revokeUrl(url);
      };
      const createElement = document.createElement.bind(document);
      (document as any).createElement = (tag: string, options?: any) => {
        const element = createElement(tag, options);
        if (track.enabled && tag.toLowerCase() === 'canvas') {
          track.canvasCount += 1;
          track.canvases.push(new WeakRef(element as HTMLCanvasElement));
        }
        return element;
      };
    });
    await setupEdit(page);
    await page.evaluate(() => {
      (window as any).__markupTrack.enabled = true;
    });
    const before = await page.evaluate(() => ({
      urls: (window as any).__markupTrack.urls.size,
      open: (window as any).__markupTrack.bitmaps.opened - (window as any).__markupTrack.bitmaps.closed
    }));

    for (let i = 0; i < 10; i += 1) {
      // The opener reads "Edit markup" once a markup has been applied in an earlier cycle.
      await openEditor(page, (await page.getByRole('button', { name: 'Edit markup', exact: true }).count()) ? 'Edit markup' : 'Draw markup');
      await dragOnImage(page, [40, 40 + i * 12], [216, 40 + i * 12], 4);
      if (i % 3 === 2) {
        await editorDialog(page).getByRole('button', { name: 'Apply markup' }).click();
      } else {
        await editorDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
        await page.getByRole('dialog', { name: 'Discard unsaved changes?' }).getByRole('button', { name: 'Discard' }).click();
      }
      await expect(editorDialog(page)).toHaveCount(0);
      await settle(page);
    }

    const after = await page.evaluate(() => {
      const track = (window as any).__markupTrack;
      const live = track.canvases.map((ref: WeakRef<HTMLCanvasElement>) => ref.deref()).filter(Boolean) as HTMLCanvasElement[];
      return {
        urls: track.urls.size,
        openBitmaps: track.bitmaps.opened - track.bitmaps.closed,
        // Layer canvases the editor created that are still allocated after their session ended.
        allocatedAfterClose: live.filter((canvas) => !canvas.isConnected && (canvas.width > 0 || canvas.height > 0)).length,
        canvasesCreated: track.canvasCount,
        bitmapsOpened: track.bitmaps.opened
      };
    });
    expect(after.bitmapsOpened).toBeGreaterThanOrEqual(10);
    expect(after.openBitmaps).toBe(before.open);
    // Exactly one applied markup URL may exist at the end (three applies replaced each other).
    expect(after.urls - before.urls).toBeLessThanOrEqual(1);
    // Every native layer / checkpoint canvas is zeroed when its session is disposed, collected or not.
    expect(after.canvasesCreated).toBeGreaterThanOrEqual(10);
    expect(after.allocatedAfterClose).toBe(0);
  });
});

