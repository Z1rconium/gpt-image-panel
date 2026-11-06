import { expect, test } from '@playwright/test';
import { loadApp, PNG_BYTES } from './fixtures/mockApi';

for (const model of ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst']) {
  test(`${model} scalar wire and special output controls`, async ({ page }) => {
    await loadApp(page);
    await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption(model);
    await page.getByRole('combobox', { name: 'Complexity', exact: true }).selectOption('low');
    await page.getByRole('combobox', { name: 'Quality', exact: true }).selectOption('max');
    await page.getByRole('combobox', { name: 'Format', exact: true }).selectOption('gif');
    await expect(page.getByRole('spinbutton', { name: 'Frames', exact: true })).toHaveValue('9');
    await expect(page.getByRole('spinbutton', { name: 'Frame duration (ms)', exact: true })).toHaveValue('200');
    await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('A dancing bird');
    await page.screenshot({ path: `/tmp/${model}-desktop.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `/tmp/${model}-mobile.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const submitted = page.waitForRequest(r => new URL(r.url()).pathname === '/api/generate' && r.method() === 'POST');
    await page.getByRole('button', { name: 'Generate', exact: true }).click();
    const body = (await submitted).postDataJSON();
    expect(body).toMatchObject({ model, complexity: 'low', output_format: 'gif', frame_count: 9, frame_duration_ms: 200 });
    expect(body).not.toHaveProperty('response_format');
    expect(body).not.toHaveProperty('partial_images');
  });
}

test('markup uploads separately with overlay and no paste-back', async ({ page }) => {
  await loadApp(page);
  await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption('gpt-image-2.6-flare');
  const images = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 256; canvas.height = 256;
    const ctx = canvas.getContext('2d')!;
    const encode = async () => Array.from(new Uint8Array(await (await new Promise<Blob>(resolve => canvas.toBlob(blob => resolve(blob!), 'image/png'))).arrayBuffer()));
    ctx.fillStyle = '#d9e9dc'; ctx.fillRect(0, 0, 256, 256);
    ctx.fillStyle = '#347a60'; ctx.fillRect(64, 64, 128, 128);
    const source = await encode();
    ctx.clearRect(0, 0, 256, 256); ctx.strokeStyle = '#f04444'; ctx.lineWidth = 8; ctx.strokeRect(54, 54, 148, 148);
    return { source, markup: await encode() };
  });
  await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: Buffer.from(images.source) });
  await page.getByLabel('Upload / replace markup PNG').setInputFiles({ name: 'markup.png', mimeType: 'image/png', buffer: Buffer.from(images.markup) });
  await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
  await page.screenshot({ path: '/tmp/gpt-image-26-markup-overlay.png', fullPage: true });
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('Follow the annotation');
  const submitted = page.waitForRequest(r => new URL(r.url()).pathname === '/api/edits' && r.method() === 'POST');
  await page.getByRole('button', { name: 'Edits', exact: true }).click();
  const body = (await submitted).postDataBuffer()?.toString() || '';
  expect(body).toContain('name="markup"');
  expect(body).not.toContain('name="paste_back"');
});

test('gallery primary accepts markup and submits from-gallery', async ({ page }) => {
  await loadApp(page);
  await page.route('**/api/image/img-1.png', route => route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8CQAgAEZQFkLBpE3wAAAABJRU5ErkJggg==', 'base64') }));
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Clear prompt and describe changes', exact: true }).click();
  await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption('gpt-image-2.6-flare');
  // Existing gallery dimensions are known; create a matching transparent PNG in-browser.
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 1; canvas.height = 1;
    const context = canvas.getContext('2d')!; context.fillStyle = 'red'; context.fillRect(0, 0, 0, 0);
    return Array.from(new Uint8Array(await (await new Promise<Blob>(resolve => canvas.toBlob(b => resolve(b!), 'image/png'))).arrayBuffer()));
  });
  await page.getByLabel('Upload / replace markup PNG').setInputFiles({ name: 'markup.png', mimeType: 'image/png', buffer: Buffer.from(bytes) });
  await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('Follow the red annotation');
  const submitted = page.waitForRequest(r => new URL(r.url()).pathname.startsWith('/api/edits/from-gallery/') && r.method() === 'POST');
  await page.getByRole('button', { name: 'Edits', exact: true }).click();
  expect((await submitted).postData()).toContain('name="markup"');
});

test('markup retry verifies primary identity and restores overlay before submission', async ({ page }) => {
  const { job } = await import('./fixtures/mockApi');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8CQAgAEZQFkLBpE3wAAAABJRU5ErkJggg==', 'base64');
  await loadApp(page, { historyJobs: [{ ...job('history-markup', 'markup retry'), model: 'gpt-image-2.6-flare', operation: 'edit', markup_applied: true }] });
  await page.route('**/api/generate/history-markup/markup/restore', route => route.fulfill({ status: 200, contentType: 'image/png', body: png }));
  await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: png });
  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  const restored = page.waitForRequest(r => r.url().includes('/history-markup/markup/restore'));
  const submitted = page.waitForRequest(r => new URL(r.url()).pathname === '/api/edits' && r.method() === 'POST');
  await drawer.locator('article').filter({ hasText: 'markup retry' }).getByRole('button', { name: 'Retry' }).click();
  expect((await restored).postData()).toContain('name="source_sha256"');
  expect((await submitted).postData()).toContain('name="markup"');
  await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
});

function animatedGif64(): Buffer {
  const chunks = [Buffer.from('GIF89a'), Buffer.from([64, 0, 64, 0, 128, 0, 0, 35, 150, 95, 245, 85, 60]), Buffer.from([33, 255, 11]), Buffer.from('NETSCAPE2.0'), Buffer.from([3, 1, 0, 0, 0])];
  for (const color of [0, 1]) {
    chunks.push(Buffer.from([33, 249, 4, 0, 20, 0, 0, 0, 44, 0, 0, 0, 0, 64, 0, 64, 0, 0, 2]));
    const codes = Array.from({ length: 4096 }, () => [4, color]).flat().concat(5);
    const packed: number[] = []; let bits = 0; let count = 0;
    for (const code of codes) { bits |= code << count; count += 3; while (count >= 8) { packed.push(bits & 255); bits >>= 8; count -= 8; } }
    if (count) packed.push(bits & 255);
    for (let i = 0; i < packed.length; i += 255) { const block = packed.slice(i, i + 255); chunks.push(Buffer.from([block.length, ...block])); }
    chunks.push(Buffer.from([0]));
  }
  return Buffer.concat([...chunks, Buffer.from([59])]);
}

for (const format of ['svg', 'gif']) {
  test(`${format} original binary previews and downloads without enabling raster edits`, async ({ page }) => {
    const { baseGalleryImages } = await import('./fixtures/mockApi');
    const bytes = format === 'svg' ? Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#23965f"/><circle cx="32" cy="32" r="22" fill="#f5553c"/></svg>') : animatedGif64();
    const filename = `original.${format}`;
    await loadApp(page, {
      galleryImages: [
        {
          ...baseGalleryImages[0],
          filename,
          image_width: 64,
          image_height: 64,
          size: '64x64',
          image_url: `/api/image/${filename}`,
          thumbnail_url: format === 'gif' ? `/api/thumb/${filename}` : `/api/image/${filename}`,
          output_format: format
        }
      ]
    });
    await page.route(`**/api/image/${filename}*`, route => route.fulfill({ status: 200, contentType: format === 'svg' ? 'image/svg+xml' : 'image/gif', headers: { 'Content-Disposition': `attachment; filename="${filename}"` }, body: bytes }));
    await page.route(`**/api/download/${filename}`, route => route.fulfill({ status: 200, contentType: format === 'svg' ? 'image/svg+xml' : 'image/gif', body: bytes }));
    // Reload to ensure image requests use the original media route.
    await page.reload();
    const card = page.locator('.gallery-card').first();
    await expect(card.getByRole('button', { name: 'Edit', exact: true })).toBeDisabled();
    if (format === 'gif') {
      await expect(card.locator('.gallery-gif-badge')).toBeVisible();
      await expect(card.locator('.gallery-gif-badge')).toHaveText('GIF');
    } else {
      await expect(card.locator('.gallery-gif-badge')).toHaveCount(0);
    }
    const img = card.locator('img').first();
    await expect(img).toBeVisible();
    if (format === 'gif') {
      await expect(img).toHaveAttribute('src', `/api/image/${filename}`);
    }
    await expect.poll(() => img.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
    await page.screenshot({ path: `/tmp/gpt-image-26-${format}-preview.png`, fullPage: true });
    await img.click();
    const modal = page.locator('.mobile-lightbox-root');
    await expect(modal).toBeVisible();
    await expect(modal.locator('.lightbox-shell')).toHaveAttribute('role', 'dialog');
    if (format === 'svg') {
      await expect(modal.getByRole('button', { name: 'Describe', exact: true })).toBeDisabled();
      await expect(modal.getByRole('button', { name: 'Analyze', exact: true })).toBeDisabled();
    } else {
      await expect(modal.getByRole('button', { name: 'Analyze', exact: true })).toBeEnabled();
    }
    const original = modal.locator('img').first();
    await expect.poll(() => original.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBe(64);
    await modal.evaluate(async node => { await Promise.all(node.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {}))); });
    await page.screenshot({ path: `/tmp/gpt-image-26-${format}-lightbox.png`, fullPage: false, animations: 'disabled' });
    const link = card.getByRole('link', { name: 'Download', exact: true });
    const href = await link.getAttribute('href');
    expect(href).toContain(filename);
    const response = await page.evaluate(async (url) => { const result = await fetch(url); return { type: result.headers.get('content-type'), bytes: Array.from(new Uint8Array(await result.arrayBuffer())) }; }, href!);
    expect(response.type).toContain(format === 'svg' ? 'image/svg+xml' : 'image/gif');
    expect(Buffer.from(response.bytes)).toEqual(bytes);
  });
}

for (const knownDimensions of [true, false]) {
test(`gallery markup compares upright EXIF dimensions (metadata known: ${knownDimensions})`, async ({ page }) => {
  const { baseGalleryImages } = await import('./fixtures/mockApi');
  await loadApp(page, { galleryImages: [{ ...baseGalleryImages[0], filename: 'oriented.jpg', image_url: '/api/image/oriented.jpg', image_width: knownDimensions ? 400 : 0, image_height: knownDimensions ? 300 : 0 }] });
  const jpeg = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 400; canvas.height = 300;
    return Array.from(Uint8Array.from(atob(canvas.toDataURL('image/jpeg').split(',')[1]), c => c.charCodeAt(0)));
  });
  // Little-endian TIFF IFD with EXIF orientation 6 (90 degrees clockwise).
  const exif = Buffer.from([0xff,0xe1,0,34,69,120,105,102,0,0,73,73,42,0,8,0,0,0,1,0,18,1,3,0,1,0,0,0,6,0,0,0,0,0,0,0]);
  const original = Buffer.concat([Buffer.from(jpeg.slice(0, 2)), exif, Buffer.from(jpeg.slice(2))]);
  await page.route('**/api/image/oriented.jpg', route => route.fulfill({ status: 200, contentType: 'image/jpeg', body: original }));
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Clear prompt and describe changes', exact: true }).click();
  await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption('gpt-image-2.6-flare');
  for (const [width, height, accepted] of [[300,400,true],[400,300,false]] as const) {
    const png = await page.evaluate(async ([w,h]) => {
      const canvas = document.createElement('canvas'); canvas.width=w; canvas.height=h;
      return Array.from(new Uint8Array(await (await new Promise<Blob>(resolve => canvas.toBlob(blob => resolve(blob!), 'image/png'))).arrayBuffer()));
    }, [width,height]);
    await page.getByLabel('Upload / replace markup PNG').setInputFiles({ name: 'markup.png', mimeType: 'image/png', buffer: Buffer.from(png) });
    if (accepted) {
      await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
      await page.getByRole('button', { name: 'Remove markup', exact: true }).click();
    } else {
      await expect(page.getByRole('img', { name: 'Markup overlay' })).toHaveCount(0);
      await expect(page.getByText('Use a transparent PNG below 50 MiB, matching the primary image and no larger than 16,777,216 pixels.', { exact: true })).toBeVisible();
    }
  }
});

}

test('markup decodes local original when gallery preview uses a signed CDN URL', async ({ page }) => {
  const { baseGalleryImages } = await import('./fixtures/mockApi');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8CQAgAEZQFkLBpE3wAAAABJRU5ErkJggg==', 'base64');
  await loadApp(page, { galleryImages: [{ ...baseGalleryImages[0], filename: 'cdn-source.png', image_url: 'https://cdn.invalid/signed/opaque?sig=secret', image_width: 1, image_height: 1 }] });
  let externalFetches = 0;
  await page.route('https://cdn.invalid/**', async route => {
    if (route.request().resourceType() === 'fetch') externalFetches++;
    // Image embedding works without CORS; script fetch must not use this URL.
    await route.fulfill({ status: 200, contentType: 'image/png', body: png });
  });
  await page.route('**/api/image/cdn-source.png', route => route.fulfill({ status: 200, contentType: 'image/png', body: png }));
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Clear prompt and describe changes', exact: true }).click();
  await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption('gpt-image-2.6-flare');
  const local = page.waitForRequest(request => new URL(request.url()).pathname === '/api/image/cdn-source.png' && request.resourceType() === 'fetch');
  await page.getByLabel('Upload / replace markup PNG').setInputFiles({ name: 'markup.png', mimeType: 'image/png', buffer: png });
  await local;
  await expect(page.getByRole('img', { name: 'Markup overlay' })).toBeVisible();
  expect(externalFetches).toBe(0);
});
