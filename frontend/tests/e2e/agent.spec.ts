import { expect, test, type Page } from '@playwright/test';
import { loadApp, settingsResponse, type MockOptions } from './fixtures/mockApi';

const agentSettings = {
  ...settingsResponse,
  ai_assistant: { ...settingsResponse.ai_assistant, agent_enabled: true }
};

async function loadAgent(page: Page, options: MockOptions = {}) {
  await loadApp(page, { settings: agentSettings, ...options });
  await page.getByTestId('mode-switch').getByRole('button', { name: 'Agent', exact: true }).click();
  // The view is a lazy chunk; the first dev-server compile can take a while.
  await expect(page.getByTestId('agent-view')).toBeVisible({ timeout: 25_000 });
}

async function sendMessage(page: Page, text: string) {
  const composer = page.getByRole('combobox', { name: 'Message to the Agent' });
  await composer.fill(text);
  await composer.press('Control+Enter');
}

test('the mode switch only appears when Agent mode is enabled', async ({ page }) => {
  await loadApp(page);
  await expect(page.getByTestId('mode-switch')).toHaveCount(0);
  await expect(page.getByTestId('agent-view')).toHaveCount(0);
});

test('switching to Agent mode swaps the workspace and keeps the gallery', async ({ page }) => {
  await loadApp(page, { settings: agentSettings });
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toBeVisible();

  const agentButton = page.getByTestId('mode-switch').getByRole('button', { name: 'Agent', exact: true });
  await expect(page.getByTestId('mode-switch').getByRole('button', { name: 'Studio' })).toHaveAttribute('aria-pressed', 'true');
  await agentButton.click();

  await expect(agentButton).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('agent-view')).toBeVisible({ timeout: 25_000 });
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toHaveCount(0);
  await expect(page.getByTestId('agent-empty')).toBeVisible();
  await expect(page).toHaveURL(/mode=agent/);
  await expect(page.getByRole('heading', { name: /Gallery/i }).first()).toBeVisible();

  await page.getByTestId('mode-switch').getByRole('button', { name: 'Studio' }).click();
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(page).not.toHaveURL(/mode=agent/);
});

test('sending with Ctrl+Enter streams a reply with an image the user can open', async ({ page }) => {
  await loadAgent(page);

  const turnRequest = page.waitForRequest(
    (request) => /\/api\/agent\/conversations\/[^/]+\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST'
  );
  await sendMessage(page, 'draw a red fox in the snow');
  const request = await turnRequest;
  expect(request.postDataJSON()).toMatchObject({
    text: 'draw a red fox in the snow',
    attachments: [],
    image_params: { size: 'auto', quality: 'auto', output_format: 'png' }
  });
  expect(request.postDataJSON().client_turn_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/);

  const thread = page.getByTestId('agent-thread');
  await expect(thread.getByTestId('agent-message-user')).toContainText('draw a red fox in the snow');
  await expect(thread.getByTestId('agent-message-assistant')).toContainText('Here are your images.');
  await expect(thread.getByText('1 image request')).toBeVisible();
  const openImage = thread.getByRole('button', { name: 'Open image Round 1 · image 1' });
  await expect(openImage).toBeVisible();
  await expect(page.getByTestId('agent-send')).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Message to the Agent' })).toHaveValue('');

  await expect(page.getByRole('navigation', { name: 'Conversations' }).getByRole('button', { name: /draw a red fox/ })).toHaveAttribute('aria-current', 'true');
  await expect(page.getByRole('status').filter({ hasText: 'Reply complete.' })).toBeAttached();

  await openImage.click();
  await expect(page.getByRole('dialog').first()).toBeVisible();
});

test('typing @ offers earlier images and inserts the reference', async ({ page }) => {
  await loadAgent(page);
  await sendMessage(page, 'first');
  await expect(page.getByRole('button', { name: 'Open image Round 1 · image 1' })).toBeVisible();

  const composer = page.getByRole('combobox', { name: 'Message to the Agent' });
  await composer.fill('make it snowy @');
  const list = page.getByRole('listbox', { name: 'Images you can reference' });
  await expect(list).toBeVisible();
  await expect(composer).toHaveAttribute('aria-expanded', 'true');
  const option = list.getByRole('option', { name: 'Reference round-1-image-1' });
  await expect(option).toHaveAttribute('aria-selected', 'true');

  await composer.press('Enter');
  await expect(composer).toHaveValue('make it snowy @round-1-image-1 ');
  await expect(list).toHaveCount(0);

  const secondTurn = page.waitForRequest(
    (request) => /\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST'
  );
  await composer.press('Control+Enter');
  expect((await secondTurn).postDataJSON().text).toBe('make it snowy @round-1-image-1');
  await expect(page.getByTestId('agent-message-user').last()).toContainText('@round-1-image-1');
});

test('Escape closes the mention list without clearing the text', async ({ page }) => {
  await loadAgent(page);
  await sendMessage(page, 'first');
  await expect(page.getByRole('button', { name: 'Open image Round 1 · image 1' })).toBeVisible();

  const composer = page.getByRole('combobox', { name: 'Message to the Agent' });
  await composer.fill('use @round');
  await expect(page.getByRole('listbox', { name: 'Images you can reference' })).toBeVisible();
  await composer.press('Escape');
  await expect(page.getByRole('listbox', { name: 'Images you can reference' })).toHaveCount(0);
  await expect(composer).toHaveValue('use @round');
});

test('attaching gallery images sends them with the message', async ({ page }) => {
  await loadAgent(page);

  await page.getByRole('button', { name: 'Attach from gallery' }).click();
  const dialog = page.getByRole('dialog', { name: 'Attach gallery images' });
  await expect(dialog).toBeVisible();
  await dialog.locator('label').first().click();
  await expect(dialog.getByRole('checkbox').first()).toBeChecked();
  await dialog.getByRole('button', { name: 'Attach 1 selected' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('list', { name: 'Attached images' }).getByRole('listitem')).toHaveCount(1);

  const turnRequest = page.waitForRequest((request) => /\/turns$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
  await sendMessage(page, 'describe this');
  expect((await turnRequest).postDataJSON().attachments).toEqual([{ kind: 'gallery', image_id: 'img-1' }]);
  await expect(page.getByRole('list', { name: 'Attached images' })).toHaveCount(1);
  await expect(page.getByTestId('agent-message-user').getByRole('list', { name: 'Attached images' })).toBeVisible();
});

test('a failed reply shows the error in the thread', async ({ page }) => {
  await loadAgent(page, { agentScenario: 'fail' });
  await sendMessage(page, 'anything');
  await expect(page.getByTestId('agent-error-block')).toHaveText('The model endpoint rejected the request.');
  await expect(page.getByRole('alert').filter({ hasText: 'The model endpoint rejected the request.' }).first()).toBeVisible();
  await expect(page.getByTestId('agent-send')).toBeVisible();
});

test('Stop cancels a reply that is still running', async ({ page }) => {
  await loadAgent(page, { agentScenario: 'hold' });
  await sendMessage(page, 'take your time');

  const stop = page.getByTestId('agent-stop');
  await expect(stop).toBeVisible();
  await expect(page.getByTestId('agent-send')).toHaveCount(0);

  const cancelRequest = page.waitForRequest((request) => /\/cancel$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
  await stop.click();
  await cancelRequest;

  await expect(page.getByTestId('agent-send')).toBeVisible();
  await expect(page.getByTestId('agent-message-assistant').last()).toHaveAttribute('data-status', 'cancelled');
  await expect(page.getByTestId('agent-message-assistant').last().getByText('Stopped.', { exact: true })).toBeVisible();
});

test('reloading with a conversation in the URL restores it', async ({ page }) => {
  await loadAgent(page);
  await sendMessage(page, 'hello there');
  await expect(page.getByRole('button', { name: 'Open image Round 1 · image 1' })).toBeVisible();
  await expect(page).toHaveURL(/mode=agent&conversation=conv-1|conversation=conv-1.*mode=agent/);

  await page.reload();
  await expect(page.getByTestId('agent-view')).toBeVisible({ timeout: 25_000 });
  await expect(page.getByTestId('agent-message-user')).toContainText('hello there');
  await expect(page.getByTestId('agent-message-assistant')).toContainText('Here are your images.');
  await expect(page.getByRole('button', { name: 'Open image Round 1 · image 1' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Conversations' }).getByRole('button', { name: /hello there/ })).toHaveAttribute('aria-current', 'true');
});

test('conversations can be renamed and deleted with confirmation', async ({ page }) => {
  await loadAgent(page, {
    agentConversations: [
      {
        id: 'conv-seed',
        title: 'Seed chat',
        messages: [
          { id: 'm1', turn_id: 't1', seq: 1, round_no: 1, role: 'user', text: 'hi', blocks: [], status: 'complete', created_at: 'x', updated_at: 'x' },
          { id: 'm2', turn_id: 't1', seq: 2, round_no: 1, role: 'assistant', text: 'yo', blocks: [{ id: 't1', type: 'text', text: 'yo there' }], status: 'complete', created_at: 'x', updated_at: 'x' }
        ],
        turns: [{ id: 't1', conversationId: 'conv-seed', roundNo: 1, status: 'completed', events: [{ event: 'block.upsert', data: { block: { id: 't1', type: 'text', text: 'yo there' } } }], attachments: [] }]
      }
    ]
  });
  const list = page.getByRole('navigation', { name: 'Conversations' });
  await list.getByRole('button', { name: 'Seed chat' }).click();
  await expect(page.getByTestId('agent-message-assistant')).toContainText('yo there');

  await list.getByRole('button', { name: 'Rename' }).click();
  const input = list.getByRole('textbox', { name: 'Conversation title' });
  await input.fill('Renamed chat');
  await input.press('Enter');
  await expect(list.getByRole('button', { name: 'Renamed chat' })).toBeVisible();

  await list.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('dialog', { name: 'Delete this conversation?' })).toBeVisible();
  await page.getByRole('button', { name: 'Delete conversation' }).click();
  await expect(list.getByRole('button', { name: 'Renamed chat' })).toHaveCount(0);
  await expect(list.getByText('No conversations yet.')).toBeVisible();
});

test('Agent labels follow the language and the layout fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page, { settings: agentSettings, language: 'zh-CN' });
  await page.getByTestId('mode-switch').getByRole('button', { name: 'Agent', exact: true }).click();

  await expect(page.getByRole('combobox', { name: '发给 Agent 的消息' })).toBeVisible();
  await expect(page.getByRole('button', { name: '显示对话列表' })).toBeVisible();
  await expect(page.getByTestId('agent-empty')).toContainText('描述你想创作的内容');

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);

  await page.getByRole('button', { name: '显示对话列表' }).click();
  await expect(page.getByRole('navigation', { name: '对话' })).toBeVisible();
});
