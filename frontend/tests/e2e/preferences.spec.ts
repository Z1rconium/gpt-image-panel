import { expect, test } from '@playwright/test';
import { job, loadApp, settingsResponse } from './fixtures/mockApi';

function isGenerateRequest(request: import('@playwright/test').Request) {
  return new URL(request.url()).pathname === '/api/generate' && request.method() === 'POST';
}

test('clear-after-submit empties the prompt only when enabled', async ({ page }) => {
  await loadApp(page);
  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });

  await prompt.fill('keep me');
  const first = page.waitForRequest(isGenerateRequest);
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await first;
  await expect(prompt).toHaveValue('keep me');

  await page.getByRole('button', { name: 'Workspace preferences' }).click();
  const dialog = page.getByRole('dialog', { name: 'Workspace preferences' });
  await dialog.getByRole('checkbox', { name: 'Clear the prompt after submitting' }).check();
  await dialog.getByRole('button', { name: 'Close' }).click();

  await prompt.fill('clear me');
  const second = page.waitForRequest(isGenerateRequest);
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  expect((await second).postDataJSON().prompt).toBe('clear me');
  await expect(prompt).toHaveValue('');

  await page.reload();
  await page.getByRole('button', { name: 'Workspace preferences' }).click();
  await expect(
    page.getByRole('dialog', { name: 'Workspace preferences' }).getByRole('checkbox', { name: 'Clear the prompt after submitting' })
  ).toBeChecked();
});

test('retry pins the job API preset only when the preference is on', async ({ page }) => {
  const settings = {
    ...settingsResponse,
    presets: [
      ...settingsResponse.presets,
      { ...settingsResponse.presets[0], id: 'alt', name: 'Alt gateway' }
    ]
  };
  const historyJob = { ...job('history-alt', 'retry on alt'), api_preset_id: 'alt', api_preset_name: 'Alt gateway' };
  await loadApp(page, { settings, historyJobs: [historyJob] });

  async function retryAndCaptureBody() {
    await page.getByRole('button', { name: 'Job History' }).click();
    const drawer = page.getByRole('dialog', { name: 'Job History' });
    await drawer.getByRole('button', { name: 'History', exact: true }).click();
    const request = page.waitForRequest(isGenerateRequest);
    await drawer.locator('article').filter({ hasText: 'retry on alt' }).getByRole('button', { name: 'Retry' }).click();
    return (await request).postDataJSON() as Record<string, unknown>;
  }

  expect((await retryAndCaptureBody()).api_preset_id).toBeUndefined();

  await page.getByRole('button', { name: 'Workspace preferences' }).click();
  const dialog = page.getByRole('dialog', { name: 'Workspace preferences' });
  await dialog.getByRole('checkbox', { name: "Retry with the job's API preset" }).check();
  await dialog.getByRole('button', { name: 'Close' }).click();

  const pinned = await retryAndCaptureBody();
  expect(pinned.api_preset_id).toBe('alt');
  expect(pinned.prompt).toBe('retry on alt');
});

test('size dialog groups sizes by tier and saves named presets', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('button', { name: 'Size', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Image Size' });

  await expect(dialog.getByRole('region', { name: '1K' }).getByRole('button')).toHaveText(['1024x1024', '1024x1536', '1536x1024']);
  await expect(dialog.getByRole('region', { name: '2K' })).toContainText('2560x1440');
  await expect(dialog.getByRole('region', { name: '4K' }).getByRole('button')).toHaveText(['3840x2160', '2160x3840']);
  await expect(dialog.getByRole('region', { name: 'My presets' })).toContainText('No saved presets yet');

  await dialog.getByLabel('Size', { exact: true }).fill('1152x2048');
  await dialog.getByLabel('Preset name').fill('Phone wallpaper');
  await dialog.getByRole('button', { name: 'Save as preset' }).click();
  const saved = dialog.getByRole('button', { name: 'Phone wallpaper · 1152x2048' });
  await expect(saved).toBeVisible();

  await dialog.getByLabel('Preset name').fill('phone WALLPAPER');
  await dialog.getByRole('button', { name: 'Save as preset' }).click();
  await expect(dialog.getByRole('alert')).toContainText('already exists');

  await saved.click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('button', { name: 'Size', exact: true })).toContainText('1152x2048');

  await page.reload();
  await page.getByRole('button', { name: 'Size', exact: true }).click();
  const reopened = page.getByRole('dialog', { name: 'Image Size' });
  await expect(reopened.getByRole('button', { name: 'Phone wallpaper · 1152x2048' })).toBeVisible();
  await reopened.getByRole('button', { name: 'Delete preset Phone wallpaper' }).click();
  await expect(reopened.getByRole('button', { name: 'Phone wallpaper · 1152x2048' })).toHaveCount(0);
});

test('preset settings send the prevent-prompt-rewriting flag', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  const checkbox = drawer.getByLabel('Prevent prompt rewriting');
  await expect(checkbox).not.toBeChecked();
  await checkbox.check();
  const save = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST'
  );
  await drawer.getByRole('button', { name: 'Save Preset' }).click();
  expect((await save).postDataJSON().prompt_guard).toBe(true);
});
