import { expect, test } from '@playwright/test';
import { loadApp } from './fixtures/mockApi';

test('Ctrl/Cmd + Enter submits once, plain Enter keeps a newline', async ({ page }) => {
  await loadApp(page, { language: 'en' });

  const prompt = page.locator('#prompt');
  await prompt.fill('submit via shortcut');

  const generateRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/generate' && request.method() === 'POST'
  );
  await prompt.press('Control+Enter');
  await generateRequest;

  await expect(prompt).toHaveValue('submit via shortcut');
  await expect(page.locator('textarea#prompt')).toBeFocused();

  await prompt.press('Enter');
  await expect(prompt).toHaveValue('submit via shortcut\n');
});
