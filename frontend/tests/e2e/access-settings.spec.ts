import { expect, test } from '@playwright/test';
import {
  PNG_BYTES,
  baseGalleryImages,
  galleryResponse,
  job,
  json,
  loadApp,
  manyGalleryImages,
  manyJobs,
  mockApi,
  settingsResponse
} from './fixtures/mockApi';

test('access gate unlocks before loading the app', async ({ page }) => {
  await mockApi(page, { authenticated: false });
  await page.goto('/');

  // Match loadApp's bootstrap allowance when this is the first cold Vite page.
  await expect(page.getByRole('heading', { name: 'Access Key' })).toBeVisible({ timeout: 15_000 });
  await page.getByLabel('Access Key').fill('open-sesame');
  await page.getByRole('button', { name: 'Unlock' }).click();

  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toBeVisible();
});

test('settings opens directly without admin access requests', async ({ page }) => {
  const adminAccessRequests: string[] = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/api/access/admin')) adminAccessRequests.push(pathname);
  });

  await loadApp(page);
  await page.getByRole('button', { name: 'Settings' }).click();

  await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
  expect(adminAccessRequests).toEqual([]);
});

test('startup data and latest version requests do not wait for access or current version responses', async ({ page }) => {
  await mockApi(page);

  let markAccessStarted: () => void = () => {};
  let releaseAccess: () => void = () => {};
  const accessStarted = new Promise<void>((resolve) => {
    markAccessStarted = resolve;
  });
  const accessGate = new Promise<void>((resolve) => {
    releaseAccess = resolve;
  });
  await page.route('**/api/access/status', async (route) => {
    markAccessStarted();
    await accessGate;
    await route.fulfill(json({ authenticated: true, expires_at: '2026-05-18T14:00:00Z' }));
  });

  let markVersionStarted: () => void = () => {};
  let releaseVersion: () => void = () => {};
  const versionStarted = new Promise<void>((resolve) => {
    markVersionStarted = resolve;
  });
  const versionGate = new Promise<void>((resolve) => {
    releaseVersion = resolve;
  });
  await page.route('**/api/version', async (route) => {
    markVersionStarted();
    await versionGate;
    await route.fulfill(json({ version: 'v0.test', github_repo: 'test/repo', release_url: null }));
  });

  const settingsRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/settings');
  const jobsRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/generate/jobs');
  const galleryRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/gallery/search');
  const latestVersionRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/version/latest');

  try {
    await page.goto('/');
    await Promise.all([
      accessStarted,
      versionStarted,
      settingsRequest,
      jobsRequest,
      galleryRequest,
      latestVersionRequest
    ]);
  } finally {
    releaseAccess();
    releaseVersion();
  }

  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
});

test('theme ignores legacy overrides and follows system preference changes in real time', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await loadApp(page);

  const root = page.locator('html');

  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect(root).toHaveClass(/dark/);

  await page.evaluate(() => window.localStorage.setItem('gpt-image-panel-theme', 'light'));
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect(root).toHaveClass(/dark/);
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();

  await page.emulateMedia({ colorScheme: 'light' });
  await expect(root).toHaveAttribute('data-theme', 'light');
  await expect(root).not.toHaveClass(/dark/);

  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect(root).toHaveClass(/dark/);
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#09090b');
});

test('image size dialog follows the light theme and preserves its dark palette', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await loadApp(page);

  await page.getByRole('button', { name: 'Size', exact: true }).click();
  const sizeDialog = page.getByRole('dialog', { name: 'Image Size' });
  const selectedPreset = sizeDialog.getByRole('button', { name: 'auto', exact: true });
  const customSize = sizeDialog.getByLabel('Size', { exact: true });

  await expect(sizeDialog).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(sizeDialog.getByRole('heading', { name: 'Image Size' })).toHaveCSS('color', 'rgb(12, 10, 9)');
  await expect(selectedPreset).toHaveCSS('color', 'rgb(4, 120, 87)');
  await expect(customSize).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(customSize).toHaveCSS('color', 'rgb(28, 25, 23)');
  await page.screenshot({ path: '/tmp/gpt-image-size-light-desktop.png' });

  await sizeDialog.getByRole('button', { name: 'Close' }).click();
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.getByRole('button', { name: 'Size', exact: true }).click();

  await expect(sizeDialog).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(sizeDialog.getByRole('heading', { name: 'Image Size' })).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect(selectedPreset).toHaveCSS('color', 'rgb(209, 250, 229)');
  await expect(customSize).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(customSize).toHaveCSS('color', 'rgb(244, 244, 245)');
  await page.screenshot({ path: '/tmp/gpt-image-size-dark-desktop.png' });
});

test('settings and prompt snippets follow the active theme while open and after reopening', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await loadApp(page);

  const root = page.locator('html');
  const settingsButton = page.getByRole('button', { name: 'Settings' });
  const promptsButton = page.getByRole('button', { name: 'Prompt snippets' });

  await expect(root).toHaveAttribute('data-theme', 'light');
  await settingsButton.click();
  const settingsDrawer = page.getByRole('dialog', { name: 'Settings' });
  const settingsTitle = settingsDrawer.getByRole('heading', { name: 'Settings' });
  const settingsApiUrl = settingsDrawer.getByLabel('API URL', { exact: true });
  const r2SyncInterval = settingsDrawer.getByLabel('Sync interval hours');
  const optimizerTimeout = settingsDrawer.getByLabel('Timeout seconds');
  const assistantVisionModel = settingsDrawer.getByLabel('Assistant vision engine');
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(settingsTitle).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect(settingsApiUrl).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(settingsApiUrl).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect(r2SyncInterval).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(optimizerTimeout).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(assistantVisionModel).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
  await page.screenshot({ path: '/tmp/gpt-image-settings-light-desktop.png' });
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await promptsButton.click();
  const promptsDrawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  const promptsTitle = promptsDrawer.getByRole('heading', { name: 'Prompt Snippets' });
  const promptsSearch = promptsDrawer.getByLabel('Search snippets');
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(promptsTitle).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect(promptsSearch).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(promptsSearch).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();

  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(promptsTitle).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect(promptsSearch).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(promptsSearch).toHaveCSS('color', 'rgb(244, 244, 245)');
  await page.screenshot({ path: '/tmp/gpt-image-prompts-dark-desktop.png' });
  await promptsDrawer.getByRole('button', { name: 'Close prompt snippets' }).click();

  await settingsButton.click();
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(settingsTitle).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect(settingsApiUrl).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(root).toHaveAttribute('data-theme', 'light');
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(settingsApiUrl).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await promptsButton.click();
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await promptsDrawer.getByRole('button', { name: 'Close prompt snippets' }).click();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(root).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('dialog', { name: 'Settings' })).toHaveCSS('background-color', 'rgb(255, 255, 255)');
});

test('settings and prompt snippets preserve dark surfaces for a dark system theme', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const settingsDrawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(settingsDrawer.getByLabel('API URL', { exact: true })).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByLabel('Sync interval hours')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByLabel('Timeout seconds')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByLabel('Assistant vision engine')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByRole('heading', { name: 'R2 Backup' })).toHaveCSS('color', 'rgb(228, 228, 231)');
  await settingsDrawer.getByRole('button', { name: 'Test Prompt Optimizer' }).click();
  await expect(page.getByTestId('prompt-optimizer-health-result')).toHaveCSS('background-color', 'rgba(16, 185, 129, 0.1)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await page.getByRole('button', { name: 'Prompt snippets' }).click();
  const promptsDrawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(promptsDrawer.getByLabel('Search snippets')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(promptsDrawer.getByRole('heading', { name: 'Prompt Snippets' })).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
});

test('settings drawer saves prompt optimizer timeout seconds', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByLabel('Timeout seconds').fill('90');
  const saveRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST');
  await page.getByRole('button', { name: 'Save Preset' }).click();
  const request = await saveRequest;

  expect(request.postDataJSON().prompt_optimizer).toMatchObject({
    timeout_seconds: 90
  });
});

test('settings drawer saves R2 sync interval hours', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByLabel('Sync interval hours').fill('6');
  const saveRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST');
  await page.getByRole('button', { name: 'Save Preset' }).click();
  const request = await saveRequest;

  expect(request.postDataJSON().r2_backup).toMatchObject({
    sync_interval_hours: 6
  });
});

test('settings drawer saves the NodeImage API key reference', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  const section = drawer.locator('section').filter({ hasText: 'NodeImage Upload' });
  const apiKey = section.getByLabel('API key');

  await expect(apiKey).toHaveAttribute('type', 'password');
  await expect(section.getByRole('link', { name: 'Get API key from NodeImage' })).toHaveAttribute('href', 'https://nodeimage.com');
  await apiKey.fill('${CUSTOM_NODEIMAGE_API_KEY}');

  const saveRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST'
  );
  await drawer.getByRole('button', { name: 'Save Preset' }).click();
  const request = await saveRequest;

  expect(request.postDataJSON().nodeimage).toEqual({
    enabled: true,
    api_key: '${CUSTOM_NODEIMAGE_API_KEY}'
  });
});

test('settings drawer edits the prompt optimizer system prompt', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByRole('button', { name: 'Edit System Prompt' }).click();

  const editor = page.getByRole('dialog', { name: 'Prompt Optimizer System Prompt' });
  await expect(editor).toBeVisible();
  const prompt = editor.getByRole('textbox', { name: 'System prompt' });
  await expect(prompt).toHaveValue('Default optimizer system prompt');

  await prompt.fill('Custom optimizer system prompt');
  const saveRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/prompt/optimizer-system-prompt' && request.method() === 'POST'
  );
  await editor.getByRole('button', { name: 'Save' }).click();
  const request = await saveRequest;
  expect(request.postDataJSON()).toEqual({ system_prompt: 'Custom optimizer system prompt' });
  await expect(page.getByRole('status')).toContainText('Prompt Optimizer system prompt saved');
  await expect(editor).toBeHidden();
});

test('settings drawer tests and closes health results', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });

  await drawer.getByRole('button', { name: 'Test Prompt Optimizer' }).click();
  const optimizerHealth = page.getByTestId('prompt-optimizer-health-result');
  await expect(optimizerHealth).toBeVisible();
  await expect(optimizerHealth).toContainText('Prompt optimizer responded successfully with model gpt-4o-mini');
  await optimizerHealth.getByRole('button', { name: 'Close' }).click();
  await expect(optimizerHealth).toHaveCount(0);

  await drawer.getByRole('button', { name: 'Health check' }).click();
  const presetHealth = page.getByTestId('preset-health-result');
  await expect(presetHealth).toBeVisible();
  await presetHealth.getByRole('button', { name: 'Close' }).click();
  await expect(presetHealth).toHaveCount(0);
});

test('settings drawer edits overall config overrides', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByRole('button', { name: 'Overall Config' }).click();

  const modal = page.getByRole('dialog', { name: 'Overall Config' });
  await expect(modal).toBeVisible();
  await expect(modal).toContainText('ENABLE_METRICS');
  await expect(modal).toContainText('WEBHOOK_SIGNING_SECRET');
  await expect(modal).toContainText('restart');
  await expect(modal).toContainText('build only');

  await modal.getByTestId('overall-config-ENABLE_METRICS').locator('input[type="checkbox"]').check();
  await modal.getByTestId('overall-config-WEBHOOK_SIGNING_SECRET').locator('input').fill('********');
  await modal.getByTestId('overall-config-ACCESS_KEY_COOKIE_NAME').getByRole('button', { name: 'Reset to .env' }).click();

  const saveRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/overall-config' && request.method() === 'PUT'
  );
  await modal.getByRole('button', { name: 'Save config' }).click();
  const request = await saveRequest;
  expect(request.postDataJSON()).toEqual({
    updates: [
      { name: 'ENABLE_METRICS', value: true },
      { name: 'WEBHOOK_SIGNING_SECRET', value: '********' },
      { name: 'ACCESS_KEY_COOKIE_NAME', clear_override: true }
    ]
  });
  await expect(page.getByRole('status')).toContainText('Overall config saved');
});

test('active preset response format default is applied to prompt form', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      default_response_format: 'b64_json',
      presets: settingsResponse.presets.map((preset) => ({
        ...preset,
        default_response_format: 'b64_json'
      }))
    }
  });

  await expect(page.getByLabel('Response format')).toHaveValue('b64_json');

  const generateRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/generate');
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('preset response format prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const request = await generateRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: 'preset response format prompt',
    response_format: 'b64_json'
  });
});

test('settings drawer deletes the active preset and switches to fallback', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      presets: [
        ...settingsResponse.presets,
        {
          ...settingsResponse.presets[0],
          id: 'alt',
          name: 'Alt preset',
          default_model: 'alt-model',
          default_response_format: 'b64_json'
        }
      ]
    }
  });

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer).toContainText('Default');
  await expect(drawer).toContainText('Alt preset');

  await drawer.getByRole('button', { name: 'Delete' }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete preset?' });
  await expect(confirm).toContainText('Delete preset "Default"?');
  await confirm.getByRole('button', { name: 'Delete' }).click();

  await expect(page.getByRole('status')).toContainText('Preset deleted');
  await expect(drawer.getByText('Default', { exact: true })).toHaveCount(0);
  await expect(drawer).toContainText('Alt preset');
  await expect(page.getByRole('main').getByRole('textbox', { name: 'Model' })).toHaveValue('alt-model');
  await expect(page.getByRole('main').getByLabel('Response format')).toHaveValue('b64_json');
});

test('link parameters offer a new preset without saving anything', async ({ page }) => {
  await mockApi(page);
  await page.goto('/?apiUrl=https%3A%2F%2Fnew-gateway.example%2Fv1&apiModel=gateway-model&apiKey=sk-leak&mode=studio');

  const drawer = page.getByRole('dialog', { name: 'Settings' });
  const banner = drawer.getByTestId('settings-prefill-banner');
  await expect(banner).toContainText('https://new-gateway.example/v1', { timeout: 15_000 });
  await expect(banner).toContainText('gateway-model');
  await expect(banner.getByTestId('settings-prefill-host')).toContainText('new-gateway.example');
  await expect(page).not.toHaveURL(/apiUrl|apiModel|apiKey/);

  const createRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/presets' && request.method() === 'POST'
  );
  await banner.getByRole('button', { name: 'Create preset' }).click();
  expect((await createRequest).postDataJSON()).toMatchObject({
    api_url: 'https://new-gateway.example/v1',
    default_model: 'gateway-model'
  });
  await expect(page.getByRole('status')).toContainText('Preset created');
  await expect(banner).toHaveCount(0);
});

test('dismissing the link suggestion creates nothing', async ({ page }) => {
  let created = false;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/settings/presets' && request.method() === 'POST') created = true;
  });
  await mockApi(page);
  await page.goto('/?apiUrl=https%3A%2F%2Fnew-gateway.example');

  const banner = page.getByTestId('settings-prefill-banner');
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await banner.getByRole('button', { name: 'Dismiss' }).click();
  await expect(banner).toHaveCount(0);
  expect(created).toBe(false);
});

test('settings drawer configures a custom async provider with a JSON mapping', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer.getByLabel('Provider mapping (JSON)')).toHaveCount(0);

  await drawer.getByLabel('Provider type').selectOption('async_json');
  await expect(drawer.getByLabel('API path')).toHaveCount(0);
  await expect(drawer.getByRole('alert')).toContainText('needs a JSON mapping');
  await expect(drawer.getByRole('button', { name: 'Save Preset' })).toBeDisabled();
  const mask = drawer.getByLabel('Supports mask inpainting');
  await expect(mask).toBeDisabled();
  await expect(mask).not.toBeChecked();

  const mapping = drawer.getByLabel('Provider mapping (JSON)');
  await mapping.fill('{oops');
  await expect(drawer.getByRole('alert')).toContainText('not a valid JSON object');
  await expect(drawer.getByRole('button', { name: 'Save Preset' })).toBeDisabled();

  const config = {
    version: 1,
    submit: { path: '/{{model}}', body: { prompt: '{{prompt}}' } },
    poll: { url_path: '$.status_url', status_path: '$.status', done: ['COMPLETED'] },
    result: { images_path: '$.images[*].url' }
  };
  await mapping.fill(JSON.stringify(config));
  await drawer.getByLabel('API URL').fill('https://queue.example.com');
  await expect(drawer.getByRole('button', { name: 'Save Preset' })).toBeEnabled();

  const saveRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST');
  await drawer.getByRole('button', { name: 'Save Preset' }).click();
  const body = (await saveRequest).postDataJSON();

  expect(body).toMatchObject({ api_url: 'https://queue.example.com', provider_kind: 'async_json' });
  expect(body.provider_config).toEqual(config);
});

test('export downloads a secret-free preset package', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });

  const downloadPromise = page.waitForEvent('download');
  await drawer.getByRole('button', { name: 'Export', exact: true }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(payload.format).toBe('gpt-image-panel-presets');
  expect(payload.presets[0].api_url).toBe('https://api.example.com');
  expect(JSON.stringify(payload)).not.toContain('api_key');
  await expect(page.getByRole('status')).toContainText('Preset exported');
});

test('import previews duplicates and applies after explicit confirmation', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByRole('button', { name: 'Import', exact: true }).click();

  const dialog = page.getByRole('dialog', { name: 'Import presets' });
  const pkg = {
    format: 'gpt-image-panel-presets',
    format_version: 1,
    presets: [
      {
        name: 'Default',
        api_url: 'https://api.example.com/v1',
        api_path: '/v1/images/generations',
        default_model: 'gpt-image-2',
        default_response_format: 'url',
        supports_mask: true,
        prompt_guard: false,
        provider_kind: 'openai'
      }
    ]
  };
  await dialog.getByLabel('Package JSON').fill(JSON.stringify(pkg));
  await dialog.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(dialog).toContainText('Same name as "Default"');
  await expect(dialog).toContainText('The API key is not included');

  const importRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/presets/import' && request.method() === 'POST'
  );
  await dialog.getByRole('button', { name: 'Apply import' }).click();
  const body = (await importRequest).postDataJSON();
  expect(body.items).toEqual([{ index: 0, action: 'create' }]);
  await expect(page.getByRole('status')).toContainText('1 preset(s) imported');
});

test('shared preset link offers a secret-free import preview', async ({ page }) => {
  const pkg = {
    format: 'gpt-image-panel-presets',
    format_version: 1,
    presets: [
      { name: 'Shared gateway', api_url: 'https://shared.example.com', api_path: '/v1/images/generations', default_model: 'shared-model', default_response_format: 'url', supports_mask: true, prompt_guard: false, provider_kind: 'openai' }
    ]
  };
  const encoded = Buffer.from(JSON.stringify(pkg), 'utf8').toString('base64url').replace(/=+$/, '');
  await mockApi(page);
  await page.goto(`/?preset=${encoded}&mode=studio`);

  const drawer = page.getByRole('dialog', { name: 'Settings' });
  const banner = drawer.getByTestId('settings-share-banner');
  await expect(banner).toContainText('Shared preset received', { timeout: 15_000 });
  await expect(page).not.toHaveURL(/preset=/);

  await banner.getByRole('button', { name: 'Preview import' }).click();
  const dialog = page.getByRole('dialog', { name: 'Import presets' });
  await expect(dialog).toContainText('Shared gateway');
  await expect(dialog).toContainText('https://shared.example.com');

  const importRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/presets/import' && request.method() === 'POST'
  );
  await dialog.getByRole('button', { name: 'Apply import' }).click();
  await importRequest;
  await expect(drawer).toContainText('Shared gateway');
});

test('preset order buttons persist the new order', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      presets: [
        ...settingsResponse.presets,
        { ...settingsResponse.presets[0], id: 'alt', name: 'Alt preset' }
      ]
    }
  });
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer).toContainText('Alt preset');

  const orderRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/presets/order' && request.method() === 'PUT'
  );
  await drawer.getByRole('button', { name: 'Move preset down' }).first().click();
  const body = (await orderRequest).postDataJSON();
  expect(body.preset_ids).toEqual(['alt', 'default']);
  await expect(page.getByRole('status')).toContainText('Preset order saved');
});

test('mapping prompt copies and sample extraction verifies the mapping', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByLabel('Provider type').selectOption('async_json');

  const promptRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/provider-mapping/prompt'
  );
  await drawer.getByRole('button', { name: 'Copy mapping prompt' }).click();
  await promptRequest;
  await expect(drawer.getByRole('button', { name: 'Prompt copied' })).toBeVisible();

  const mapping = drawer.getByLabel('Provider mapping (JSON)');
  await mapping.fill(
    JSON.stringify({
      version: 2,
      submit: { path: '/submit', body: { prompt: '{{prompt}}' } },
      poll: { task_id_path: '$.id', url_template: '/jobs/{{task_id}}', status_path: '$.state', done: ['done'] },
      result: { images_path: '$.images[*].url' }
    })
  );
  await drawer.getByText('Verify extraction with sample responses').click();
  await drawer.getByLabel('Submit response sample').fill(JSON.stringify({ id: 'job-1' }));
  await drawer.getByLabel('Poll response sample').fill(JSON.stringify({ state: 'done' }));
  const validateRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/provider-mapping/validate' && request.method() === 'POST'
  );
  await drawer.getByRole('button', { name: 'Verify mapping' }).click();
  await validateRequest;
  await expect(drawer).toContainText('job-1');
  await expect(drawer).toContainText('/jobs/job-1');
});
