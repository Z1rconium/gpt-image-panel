import { test, expect } from '@playwright/test';
import { loadApp, settingsResponse } from './fixtures/mockApi';

test('selecting an asset mention sends its collection attachment', async ({ page }) => {
  const collection = { id: 'pack-1', name: 'Hero', asset_alias: 'hero', position: 0, is_default: false, created_at: '2026-01-01', updated_at: '2026-01-01' };
  await loadApp(page, { settings: { ...settingsResponse, ai_assistant: { ...settingsResponse.ai_assistant, agent_enabled: true } }, collections: [collection] });
  await page.getByTestId('mode-switch').getByRole('button', { name: 'Agent', exact: true }).click();
  const composer = page.getByRole('combobox', { name: 'Message to the Agent' });
  await expect(composer).toBeVisible();
  await composer.fill('Use @he');
  await expect(page.getByRole('option', { name: '@hero' })).toBeVisible();
  await composer.press('Enter');
  await expect(composer).toHaveValue('Use @hero ');
  await expect(page.getByRole('list', { name: 'Asset packs' })).toContainText('@hero');
  const request = page.waitForRequest((r) => /\/turns$/.test(new URL(r.url()).pathname) && r.method() === 'POST');
  await composer.press('Control+Enter');
  expect((await request).postDataJSON().attachments).toEqual([{ kind: 'asset', collection_id: 'pack-1' }]);
});
