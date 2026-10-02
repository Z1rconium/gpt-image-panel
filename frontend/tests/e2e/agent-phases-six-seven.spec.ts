import { expect, test, type Page } from '@playwright/test';
import { loadApp, settingsResponse, type MockOptions } from './fixtures/mockApi';

const settings = { ...settingsResponse, ai_assistant: { ...settingsResponse.ai_assistant, agent_enabled: true } };

async function loadAgent(page: Page, options: MockOptions = {}) {
  await loadApp(page, { settings, ...options });
  await page.getByTestId('mode-switch').getByRole('button', { name: 'Agent', exact: true }).click();
  await expect(page.getByTestId('agent-view')).toBeVisible({ timeout: 25_000 });
}

async function send(page: Page, text: string) {
  const request = page.waitForRequest((request) => /\/agent\/conversations\/[^/]+\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
  await page.getByRole('combobox', { name: 'Message to the Agent' }).fill(text);
  await page.getByTestId('agent-send').click();
  const submitted = await request;
  await expect(page.getByTestId('agent-send')).toBeVisible();
  await expect(page.getByTestId('agent-message-assistant').last()).toHaveAttribute('data-status', 'complete');
  return submitted.postDataJSON();
}

test('editing a historical message preserves both paths, restores on reload, and continues the selected path', async ({ page }) => {
  await loadAgent(page);
  await send(page, 'original one');
  await send(page, 'original two');
  await send(page, 'original three');
  const selector = page.getByRole('combobox', { name: 'Conversation path' });
  const originalHead = await selector.inputValue();
  const second = page.getByTestId('agent-message-user').nth(1);
  await second.getByRole('button', { name: 'Edit message' }).click();
  await second.getByRole('textbox', { name: 'Edit message' }).fill('changed two');
  const edit = page.waitForRequest((request) => /\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
  await second.getByRole('button', { name: 'Send as new branch' }).click();
  expect((await edit).postDataJSON()).toMatchObject({ action: 'edit', text: 'changed two', branch_revision: 3 });
  await expect(page.getByTestId('agent-message-user')).toHaveCount(2);
  await expect(page.getByTestId('agent-message-user').last()).toContainText('changed two');
  await expect(page.getByTestId('agent-message-assistant').last()).toHaveAttribute('data-status', 'complete');
  await expect(page.getByRole('button', { name: 'Open image Round 2 · image 1' })).toBeVisible();
  const forkHead = await selector.inputValue();
  let newTurns = 0;
  page.on('request', (request) => { if (/\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST') newTurns += 1; });
  await selector.selectOption(originalHead);
  await expect(page.getByTestId('agent-message-user')).toHaveCount(3);
  await expect(page.getByTestId('agent-message-user').last()).toContainText('original three');
  await selector.selectOption(forkHead);
  await expect(page.getByTestId('agent-message-user')).toHaveCount(2);
  expect(newTurns).toBe(0);
  await page.reload();
  await expect(page.getByTestId('agent-message-user').last()).toContainText('changed two');
  await send(page, 'changed three');
  await expect(page.getByTestId('agent-message-user')).toHaveCount(3);
  await expect(page.getByTestId('agent-thread')).not.toContainText('original three');
});

test('regenerate creates a sibling and leaves the original answer accessible', async ({ page }) => {
  await loadAgent(page);
  await send(page, 'actual prompt');
  const selector = page.getByRole('combobox', { name: 'Conversation path' });
  const originalHead = await selector.inputValue();
  const request = page.waitForRequest((request) => /\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ action: 'regenerate', source_turn_id: originalHead, branch_revision: 1 });
  await expect(selector).not.toHaveValue(originalHead);
  await expect(page.getByTestId('agent-message-user')).toHaveCount(1);
  await expect(page.getByTestId('agent-message-user')).toContainText('actual prompt');
  await selector.selectOption(originalHead);
  await expect(selector).toHaveValue(originalHead);
  await expect(page.getByRole('button', { name: 'Open image Round 1 · image 1' })).toBeVisible();
});

test('uncertain submission retries reuse the client key and captured branch revision', async ({ page }) => {
  await loadAgent(page);
  const attempts: Record<string, unknown>[] = [];
  await page.route('**/api/agent/conversations/*/turns', async (route) => {
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) await route.abort('failed');
    else await route.fallback();
  });
  await page.getByRole('combobox', { name: 'Message to the Agent' }).fill('retry this once');
  await page.getByTestId('agent-send').click();
  await expect(page.getByTestId('agent-error')).toBeVisible();
  await page.getByTestId('agent-send').click();
  await expect(page.getByTestId('agent-message-assistant')).toHaveAttribute('data-status', 'complete');
  expect(attempts).toHaveLength(2);
  expect(attempts[1].client_turn_id).toBe(attempts[0].client_turn_id);
  expect(attempts[1].branch_revision).toBe(attempts[0].branch_revision);
  await expect(page.getByTestId('agent-message-user')).toHaveCount(1);
});

test('switching away from a running path does not cancel it or show its replay', async ({ page }) => {
  await loadAgent(page, { agentScenario: 'hold' });
  await page.getByRole('combobox', { name: 'Message to the Agent' }).fill('still running');
  await page.getByTestId('agent-send').click();
  await expect(page.getByTestId('agent-stop')).toBeVisible();
  const selector = page.getByRole('combobox', { name: 'Conversation path' });
  const head = await selector.inputValue();
  let cancels = 0;
  page.on('request', (request) => { if (/\/cancel$/.test(new URL(request.url()).pathname)) cancels += 1; });
  await selector.selectOption('');
  await expect(page.getByTestId('agent-message-assistant')).toHaveCount(0);
  await expect(page.getByTestId('agent-stop')).toBeVisible();
  expect(cancels).toBe(0);
  await selector.selectOption(head);
  await expect(page.getByTestId('agent-message-assistant')).toContainText('Here are your images.');
  await page.getByTestId('agent-stop').click();
  await expect(page.getByTestId('agent-send')).toBeVisible();
  expect(cancels).toBe(1);
});

test('search status, real citation positions, and safe Markdown survive reload and path changes', async ({ page }) => {
  await loadAgent(page, { agentScenario: 'search', settings: { ...settings, ai_assistant: { ...settings.ai_assistant, api_path: '/v1/responses', agent_web_search_enabled: true, agent_web_search_supported: true } } });
  await expect(page.getByTestId('agent-search-capability')).toContainText('enabled');
  await send(page, 'what is the capital?');
  const markdown = page.getByTestId('agent-markdown');
  await expect(markdown.getByRole('heading', { name: 'Verified answer' })).toBeVisible();
  await expect(markdown.locator('table')).toBeVisible();
  await expect(markdown.locator('pre code')).toContainText('const city');
  await expect(markdown).toContainText('<script>window.agentUnsafe = true</script>');
  expect(await page.evaluate(() => (window as Window & { agentUnsafe?: boolean }).agentUnsafe)).toBeUndefined();
  await expect(markdown.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(markdown.getByRole('link', { name: '1', exact: true })).toHaveAttribute('href', 'https://example.org/paris');
  await expect(page.getByTestId('agent-search-status')).toContainText('Search complete');
  const sources = page.getByTestId('agent-sources');
  await sources.locator('summary').click();
  await expect(sources.getByRole('link', { name: 'Official city guide' })).toBeVisible();
  const selector = page.getByRole('combobox', { name: 'Conversation path' });
  const head = await selector.inputValue();
  await selector.selectOption('');
  await expect(sources).toHaveCount(0);
  await selector.selectOption(head);
  await expect(sources).toContainText('Official city guide');
  await page.reload();
  await expect(sources).toContainText('Official city guide');
  await expect(markdown.getByRole('link', { name: '1', exact: true })).toBeVisible();
});

test('unsupported search is explained before submission and never starts a turn', async ({ page }) => {
  await loadAgent(page, { settings: { ...settings, ai_assistant: { ...settings.ai_assistant, agent_web_search_enabled: true, agent_web_search_supported: false } } });
  await expect(page.getByTestId('agent-search-capability')).toContainText('supported model');
  await page.getByRole('combobox', { name: 'Message to the Agent' }).fill('search');
  await expect(page.getByTestId('agent-send')).toBeDisabled();
});

test('a stale second tab cannot overwrite the selected path and must resubmit after refresh', async ({ page, context }) => {
  const shared: NonNullable<MockOptions['agentConversations']> = [{ id: 'shared', title: 'Shared conversation', messages: [], turns: [] }];
  const options = { agentConversations: shared, agentShareState: true };
  await loadAgent(page, options);
  await page.getByRole('navigation', { name: 'Conversations' }).getByRole('button', { name: /Shared conversation/ }).click();
  const second = await context.newPage();
  await loadAgent(second, options);
  await second.getByRole('navigation', { name: 'Conversations' }).getByRole('button', { name: /Shared conversation/ }).click();
  await expect(second.getByRole('combobox', { name: 'Message to the Agent' })).toBeVisible();
  await send(page, 'first tab wins');
  const stale = second.waitForResponse((response) => /\/turns$/.test(new URL(response.url()).pathname));
  await second.getByRole('combobox', { name: 'Message to the Agent' }).fill('second tab draft');
  await second.getByTestId('agent-send').click();
  expect((await stale).status()).toBe(409);
  await expect(second.getByTestId('agent-error')).toContainText('selected branch changed');
  await expect(second.getByTestId('agent-message-user')).toHaveCount(1);
  await expect(second.getByTestId('agent-message-user')).toContainText('first tab wins');
  await expect(second.getByRole('combobox', { name: 'Message to the Agent' })).toHaveValue('second tab draft');
  const fresh = await send(second, 'second tab draft');
  expect(fresh.branch_revision).toBe(1);
  await expect(second.getByTestId('agent-message-user')).toHaveCount(2);
  await second.close();
});
