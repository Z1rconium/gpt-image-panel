import { expect, test } from '@playwright/test';
import { baseGalleryImages, loadApp, PNG_BYTES } from './fixtures/mockApi';

for (const model of ['gpt-image-2.5-flare', 'gpt-image-2.5-sunburst']) {
  test(`${model} selection sends max quality and preserves legacy defaults`, async ({ page }) => {
    await loadApp(page);
    const modelSelect = page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true });
    await expect(modelSelect).toHaveValue('');
    await expect(page.getByRole('main').getByRole('textbox', { name: 'Model', exact: true })).toHaveValue('preset-default-model');
    await modelSelect.selectOption(model);
    await page.getByRole('combobox', { name: 'Quality', exact: true }).selectOption('max');
    await expect(page.getByRole('combobox', { name: 'Response format', exact: true })).toBeDisabled();
    await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('A watercolor cat');
    await page.screenshot({ path: `/tmp/${model}-desktop.png`, fullPage: true });
    const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/generate' && r.method() === 'POST');
    await page.getByRole('button', { name: 'Generate', exact: true }).click();
    expect((await submitted).postDataJSON()).toMatchObject({ model, quality: 'max', response_format: null });
  });
}

test('quality resets on legacy model and custom snapshots retain extended quality', async ({ page }) => {
  await loadApp(page);
  const model = page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true });
  await model.selectOption('gpt-image-2.5-flare');
  await page.getByRole('combobox', { name: 'Quality', exact: true }).selectOption('xhigh');
  await model.selectOption('gpt-image-2');
  await expect(page.getByRole('combobox', { name: 'Quality', exact: true })).toHaveValue('auto');
  await expect(page.getByText('Quality reset to auto', { exact: false })).toBeVisible();
  await model.selectOption('');
  await page.getByRole('main').getByRole('textbox', { name: 'Model', exact: true }).fill('gpt-image-2.5-sunburst-2026-09-08');
  await page.getByRole('combobox', { name: 'Quality', exact: true }).selectOption('max');
  await expect(page.getByRole('combobox', { name: 'Quality', exact: true })).toHaveValue('max');
});

test('32000 Unicode characters submit intact and over-limit input is not truncated', async ({ page }) => {
  await loadApp(page);
  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await prompt.fill('🐈'.repeat(32001));
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByText('Prompt must contain at most 32,000 characters.', { exact: true })).toBeVisible();
  await expect(prompt).toHaveValue('🐈'.repeat(32001));
  await prompt.fill('🐈'.repeat(32000));
  const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/generate' && r.method() === 'POST');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  expect((await submitted).postDataJSON().prompt).toBe('🐈'.repeat(32000));
});

test('2.5 editing sends background and extended quality', async ({ page }) => {
  await loadApp(page);
  await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption('gpt-image-2.5-sunburst');
  await page.getByRole('combobox', { name: 'Quality', exact: true }).selectOption('xhigh');
  await page.getByRole('combobox', { name: 'Background', exact: true }).selectOption('transparent');
  await page.getByLabel('Upload edit image').setInputFiles({ name: 'source.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('Keep the subject and remove the background');
  const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/edits' && r.method() === 'POST');
  await page.getByRole('button', { name: 'Edits', exact: true }).click();
  const body = (await submitted).postDataBuffer()?.toString() || '';
  expect(body).toContain('gpt-image-2.5-sunburst');
  expect(body).toContain('xhigh');
  expect(body).toContain('transparent');
  expect(body).not.toContain('name="response_format"');
});

test('2.5 preset selection is saved without changing the API path', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByRole('combobox', { name: 'Default model', exact: true }).selectOption('gpt-image-2.5-flare');
  const submitted = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/settings' && r.method() === 'POST');
  await drawer.getByRole('button', { name: 'Save Preset' }).click();
  expect((await submitted).postDataJSON()).toMatchObject({ default_model: 'gpt-image-2.5-flare', api_path: '/v1/images/generations' });
});

test('gallery reuse preserves a 2.5 snapshot and max quality on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page, { galleryImages: [{ ...baseGalleryImages[0], model: 'gpt-image-2.5-sunburst-2026-09-08', quality: 'max', api_path: '/v1/images/generations' }] });
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Use all', exact: true }).click();
  await expect(page.getByRole('main').getByRole('textbox', { name: 'Model', exact: true })).toHaveValue('gpt-image-2.5-sunburst-2026-09-08');
  await expect(page.getByRole('combobox', { name: 'Quality', exact: true })).toHaveValue('max');
  await page.screenshot({ path: '/tmp/gpt-image-25-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('invalid API path and invalid image dimensions are blocked locally', async ({ page }) => {
  await loadApp(page);
  await page.locator('section').filter({ has: page.locator('#prompt') }).getByRole('combobox', { name: 'Model', exact: true }).selectOption('gpt-image-2.5-flare');
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('A cat');
  await page.getByRole('combobox', { name: 'API path', exact: true }).selectOption('/v1/responses');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByText('GPT Image 2.5 requires the Images API.', { exact: false })).toBeVisible();
  await page.getByRole('combobox', { name: 'API path', exact: true }).selectOption('/v1/images/generations');
  await page.getByRole('button', { name: 'Size', exact: true }).click();
  await page.locator('#custom-size').fill('1024x0');
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'multiples of 16' })).toBeVisible();
});
