import { expect, test, type Locator, type Page } from '@playwright/test';
import { baseGalleryImages, job, loadApp, PNG_BYTES } from './fixtures/mockApi';

async function pointer(target: Locator, type: string, pointerId: number, x: number, y: number) {
  await target.dispatchEvent(type, { pointerId, pointerType: 'touch', isPrimary: pointerId === 1, clientX: x, clientY: y, bubbles: true });
}

async function installStreams(page: Page) {
  await page.addInitScript(() => {
    const streams: { url: string; emit: (event: string, data: unknown) => void }[] = [];
    class Stream extends EventTarget {
      static CONNECTING = 0;
      static CLOSED = 2;
      readyState = 1;
      onerror = null;
      constructor(public url: string) { super(); streams.push(this); }
      close() { this.readyState = 2; }
      emit(event: string, data: unknown) {
        if (this.readyState !== 2) this.dispatchEvent(new MessageEvent(event, { data: JSON.stringify(data) }));
      }
    }
    Object.assign(window, { EventSource: Stream, __streams: streams });
  });
}

async function emit(page: Page, url: string, event: string, data: unknown) {
  await page.evaluate(({ url, event, data }) => {
    const streams = (window as unknown as { __streams: { url: string; emit: (event: string, data: unknown) => void }[] }).__streams;
    streams.filter((stream) => stream.url === url).forEach((stream) => stream.emit(event, data));
  }, { url, event, data });
}

test('modifier and band selection preserve ordinary preview and filtered scope', async ({ page }) => {
  await loadApp(page);
  const cards = page.locator('.gallery-card');
  await cards.first().locator('.gallery-media-well').click({ modifiers: ['Control'] });
  await expect(page.getByText('1 selected on this page', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Image Details' })).toBeHidden();
  await page.getByRole('button', { name: 'Cancel selection' }).click();
  await cards.first().scrollIntoViewIfNeeded();
  const rect = (await cards.first().boundingBox())!;
  await page.mouse.move(rect.x + 10, rect.y + 10);
  await page.mouse.down();
  await page.mouse.move(rect.x + rect.width - 10, rect.y + Math.min(rect.height - 10, 180), { steps: 8 });
  await page.mouse.up();
  await expect(page.getByRole('button', { name: 'Cancel selection' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Image Details' })).toBeHidden();
  await page.getByRole('button', { name: 'Select filtered' }).click();
  await expect(page.getByRole('button', { name: 'Select page' })).toBeDisabled();
  await cards.first().locator('.gallery-media-well').click({ modifiers: ['Control'] });
  await expect(page.getByText('2 selected from current filters', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Exit select-all' }).click();
  await expect(page.getByRole('button', { name: 'Select page' })).toBeEnabled();
});

test('touch selection commits once and vertical scrolling cannot become a selection', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page);
  const media = page.locator('.gallery-media-well').first();
  await pointer(media, 'pointerdown', 1, 100, 200);
  await pointer(media, 'pointermove', 1, 160, 202);
  await pointer(media, 'pointermove', 1, 180, 203);
  await pointer(media, 'pointerup', 1, 180, 203);
  await expect(page.getByText('1 selected on this page', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel selection' }).click();
  await pointer(media, 'pointerdown', 1, 100, 200);
  await pointer(media, 'pointermove', 1, 103, 240);
  await pointer(media, 'pointermove', 1, 190, 242);
  await pointer(media, 'pointerup', 1, 190, 242);
  await expect(page.getByRole('button', { name: 'Select', exact: true })).toBeVisible();
});

test('single-image lightbox supports pinch, pan, double tap and long-press actions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page, { galleryImages: [baseGalleryImages[0]] });
  await page.locator('.gallery-media-well').click();
  const media = page.locator('.lightbox-media');
  await expect(media).toBeVisible();
  await pointer(media, 'pointerdown', 1, 120, 180);
  await pointer(media, 'pointerdown', 2, 180, 180);
  await pointer(media, 'pointermove', 2, 240, 180);
  await expect(page.locator('.lightbox-image-stage')).toHaveCSS('transform', /matrix\(2,/);
  await pointer(media, 'pointerup', 2, 240, 180);
  await pointer(media, 'pointermove', 1, 140, 190);
  await pointer(media, 'pointerup', 1, 140, 190);
  await expect(page.getByRole('dialog', { name: 'Image Details' })).toBeVisible();
  await page.getByRole('button', { name: 'Fit to window' }).click();
  for (let index = 0; index < 2; index++) {
    await pointer(media, 'pointerdown', 1, 150, 180);
    await pointer(media, 'pointerup', 1, 150, 180);
  }
  await expect(page.locator('.lightbox-image-stage')).toHaveCSS('transform', /matrix\(2.5,/);
  await page.getByRole('button', { name: 'Fit to window' }).click();
  await pointer(media, 'pointerdown', 1, 150, 180);
  const menu = page.getByRole('menu', { name: 'Image actions' });
  await expect(menu).toBeVisible();
  await pointer(media, 'pointerup', 1, 150, 180);
  await expect(menu.getByRole('menuitem', { name: 'Download', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(page.getByRole('dialog', { name: 'Image Details' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Image Details' })).toBeHidden();
});

test('collection overview uses batched covers and retains other filters', async ({ page }) => {
  const details: string[] = [];
  page.on('request', (request) => { if (/\/api\/gallery\/img-\d+$/.test(new URL(request.url()).pathname)) details.push(request.url()); });
  await loadApp(page, { collections: [{ id: 'album', name: 'Album', position: 0, is_default: true, created_at: 'now', updated_at: 'now' }], collectionItems: { album: ['img-1'] } });
  await page.getByLabel('Filter prompt').fill('First');
  await expect(page.locator('.gallery-card')).toHaveCount(1);
  await page.getByRole('button', { name: 'Collection overview', exact: true }).click();
  await page.getByRole('button', { name: 'Album · 1 image', exact: true }).click();
  await expect(page.getByLabel('Filter prompt')).toHaveValue('First');
  await expect(page.getByLabel('Collection', { exact: true })).toHaveValue('album');
  expect(details).toHaveLength(0);
});

test('completion notification permission is requested only on explicit opt-in', async ({ page }) => {
  await page.addInitScript(() => {
    let requests = 0;
    class NotificationStub {
      static permission = 'denied';
      static async requestPermission() { requests++; return 'denied'; }
    }
    Object.assign(window, { Notification: NotificationStub, __permissionRequests: () => requests });
  });
  await loadApp(page);
  await page.getByRole('button', { name: 'Workspace preferences' }).click();
  const dialog = page.getByRole('dialog', { name: 'Workspace preferences' });
  const checkbox = dialog.getByRole('checkbox', { name: 'Notify when tasks finish' });
  await expect(checkbox).not.toBeChecked();
  expect(await page.evaluate(() => (window as unknown as { __permissionRequests: () => number }).__permissionRequests())).toBe(0);
  await checkbox.click();
  await expect(checkbox).not.toBeChecked();
  await expect(dialog).toContainText('Notification permission was denied');
  expect(await page.evaluate(() => (window as unknown as { __permissionRequests: () => number }).__permissionRequests())).toBe(1);
  await dialog.getByRole('button', { name: 'Close' }).click();
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('permission refusal does not block generation');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();
});

test('multi-image preview keeps a running slot after another unit finishes and clears on terminal', async ({ page }) => {
  await installStreams(page);
  const running = { ...job('job-streamed', 'streamed'), status: 'running', n: 2, streaming: true, images: [], image_id: null, image_url: null };
  await loadApp(page, { runningJobs: [running], generatedJob: running });
  await expect.poll(() => page.evaluate(() => (window as unknown as { __streams: { url: string }[] }).__streams.some((stream) => stream.url === '/api/generate/job-streamed/events'))).toBe(true);
  await emit(page, '/api/generate/job-streamed/events', 'job', running);
  await expect(page.getByText('Waiting for preview').first()).toBeVisible();
  const preview = { job_id: running.job_id, partial_image_index: 0, call_index: 0, sequence: 1, mime_type: 'image/png', data_url: `data:image/png;base64,${PNG_BYTES.toString('base64')}` };
  await emit(page, '/api/generate/job-streamed/events', 'preview', { ...preview, unit_index: 0 });
  await emit(page, '/api/generate/job-streamed/events', 'preview', { ...preview, unit_index: 1 });
  await expect(page.getByRole('img', { name: 'Image 2 streaming preview' })).toBeVisible();
  const burstMs = await page.evaluate(async (preview) => {
    const streams = (window as unknown as { __streams: { url: string; emit: (event: string, data: unknown) => void }[] }).__streams;
    const stream = streams.find((stream) => stream.url === '/api/generate/job-streamed/events')!;
    const started = performance.now();
    for (let index = 0; index < 400; index++) stream.emit('preview', { ...preview, unit_index: index % 2, sequence: index + 2 });
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    return performance.now() - started;
  }, preview);
  console.log(`Synthetic 400-frame / 2-slot burst through two animation frames: ${burstMs.toFixed(1)} ms`);
  await expect(page.locator('#preview-panel img[alt$="streaming preview"]')).toHaveCount(2);
  const finalImage = { image_id: 'done', image_url: '/api/image/img-1.png', filename: 'img-1.png', unit_index: 0 };
  await emit(page, '/api/generate/job-streamed/events', 'job', { ...running, images: [finalImage], unit_statuses: { '0': 'success', '1': 'running' } });
  await expect(page.getByRole('img', { name: 'Image 2 streaming preview' })).toBeVisible();
  await emit(page, '/api/generate/job-streamed/events', 'job', { ...running, status: 'partial_failure', images: [finalImage], failure_count: 1, success_count: 1 });
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Image 2 streaming preview' })).toBeHidden();
});

test('simultaneous terminal events notify once across tabs and a click opens the result', async ({ page, context }) => {
  const final = { ...job('job-notification', 'notification result'), status: 'partial_failure', n: 2, success_count: 1, failure_count: 1 };
  const running = { ...final, status: 'running', images: [], image_id: null, image_url: null };
  const other = { ...running, job_id: 'job-other', n: 1, prompt: 'another running task' };
  const second = await context.newPage();
  for (const tab of [page, second]) {
    await installStreams(tab);
    await tab.addInitScript(() => {
      const notifications: { title: string; options: NotificationOptions; onclick?: () => void }[] = [];
      class NotificationStub {
        static permission = 'granted';
        onclick?: () => void;
        constructor(public title: string, public options: NotificationOptions) { notifications.push(this); }
        close() {}
      }
      document.hasFocus = () => false;
      localStorage.setItem('gpt-image-panel-preferences', JSON.stringify({ version: 1, taskCompletionNotifications: true }));
      Object.assign(window, { Notification: NotificationStub, __notifications: notifications });
    });
    await loadApp(tab, { runningJobs: [other, running], generatedJobs: [final, other] });
    await expect.poll(() => tab.evaluate(() => (window as unknown as { __streams: { url: string; readyState: number }[] }).__streams.some((stream) => stream.url === '/api/generate/job-other/events' && stream.readyState !== 2))).toBe(true);
  }
  const notificationCount = (tab: Page) => tab.evaluate(() => (window as unknown as { __notifications: unknown[] }).__notifications.length);
  await Promise.all([page, second].map((tab) => emit(tab, '/api/generate/jobs/events', 'job', final)));
  await expect.poll(async () => (await notificationCount(page)) + (await notificationCount(second))).toBe(1);
  const winner = (await notificationCount(page)) ? page : second;
  expect(await winner.evaluate(() => (window as unknown as { __notifications: { options: NotificationOptions }[] }).__notifications[0].options.body)).toContain('1 succeeded, 1 failed');
  await winner.evaluate(() => (window as unknown as { __notifications: { onclick: () => void }[] }).__notifications[0].onclick());
  await expect(winner.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  await emit(winner, '/api/generate/job-other/events', 'job', other);
  await expect(winner.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  expect(await winner.evaluate(() => (window as unknown as { __streams: { url: string; readyState: number }[] }).__streams.filter((stream) => stream.url === '/api/generate/job-other/events').every((stream) => stream.readyState === 2))).toBe(true);
  await Promise.all([page, second].map((tab) => emit(tab, '/api/generate/jobs/events', 'job', final)));
  expect((await notificationCount(page)) + (await notificationCount(second))).toBe(1);
  await winner.reload();
  await expect(winner.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await emit(winner, '/api/generate/jobs/events', 'job', final);
  await winner.evaluate(() => navigator.locks.request('gpt-image-panel-notified-jobs', () => {}));
  expect(await notificationCount(winner)).toBe(0);
});
