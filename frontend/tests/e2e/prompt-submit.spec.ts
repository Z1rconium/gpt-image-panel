import { expect, test, type Page } from '@playwright/test';
import { job, loadApp, PNG_BYTES } from './fixtures/mockApi';

type JobScenario = {
  /** Per-job SSE payloads; a job with no entry stays silent (still running). */
  events?: Record<string, unknown>;
  /** Per-job ordered snapshots, emitted at `sequenceIntervalMs` apart. */
  sequences?: Record<string, unknown[]>;
  /** Per-job delay before the first ordered snapshot (default 250 ms). */
  sequenceStartMs?: Record<string, number>;
  /** Gap between ordered snapshots (default 400 ms). */
  sequenceIntervalMs?: number;
  /** Number of jobs that should be created before the POSTs may resolve. */
  holdPosts?: boolean;
};

async function installJobStreams(page: Page, scenario: JobScenario) {
  await page.addInitScript((config: JobScenario) => {
    const state = { opened: [] as string[], closed: [] as string[] };
    class MockEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readonly withCredentials = false;
      readyState = MockEventSource.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        super();
        this.url = url;
        state.opened.push(url);
        setTimeout(() => this.open(), 0);
      }

      private open() {
        if (this.readyState === MockEventSource.CLOSED) return;
        this.readyState = MockEventSource.OPEN;
        const openEvent = new Event('open');
        this.dispatchEvent(openEvent);
        this.onopen?.(openEvent);
        if (this.url.endsWith('/api/generate/jobs/events')) return;
        const match = this.url.match(/\/api\/generate\/(job-[^/]+)\/events$/);
        if (!match) return;
        const sequence = config.sequences?.[match[1]];
        if (sequence) {
          const startMs = config.sequenceStartMs?.[match[1]] ?? 250;
          const intervalMs = config.sequenceIntervalMs ?? 400;
          sequence.forEach((payload, index) => {
            setTimeout(() => {
              if (this.readyState === MockEventSource.CLOSED) return;
              this.dispatchEvent(new MessageEvent('job', { data: JSON.stringify(payload) }));
            }, startMs + index * intervalMs);
          });
          return;
        }
        const payload = config.events?.[match[1]];
        if (!payload) return;
        setTimeout(() => {
          if (this.readyState === MockEventSource.CLOSED) return;
          this.dispatchEvent(new MessageEvent('job', { data: JSON.stringify(payload) }));
        }, 250);
      }

      close() {
        if (this.readyState === MockEventSource.CLOSED) return;
        this.readyState = MockEventSource.CLOSED;
        state.closed.push(this.url);
      }
    }
    (window as Window & { __jobStreams?: typeof state }).__jobStreams = state;
    window.EventSource = MockEventSource as unknown as typeof EventSource;
  }, scenario);
}

function streamState(page: Page) {
  return page.evaluate(
    () => (window as Window & { __jobStreams?: { opened: string[]; closed: string[] } }).__jobStreams
  );
}

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

test('repeated Generate clicks during a pending POST each create a request and a stable slot', async ({ page }) => {
  const pending = { ...job('job-pending', 'repeated prompt', 'running'), images: [], image_id: null, image_url: null, n: 1 };
  await installJobStreams(page, { events: {} });
  await loadApp(page, { generatedJobs: [pending] });

  const generateRequests: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/generate') {
      generateRequests.push(String(request.postDataJSON().prompt));
    }
  });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const generate = page.getByRole('button', { name: 'Generate', exact: true });
  await prompt.fill('repeated prompt');
  await generate.click();
  // The button stays usable while the first request is still in flight.
  await expect(generate).toBeEnabled();
  await generate.click();
  await generate.click();

  await expect.poll(() => generateRequests.length).toBe(3);
  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(3);
});

test('a finished image stays selected while a new request appends a loading thumbnail', async ({ page }) => {
  const finished = job('job-first', 'first prompt');
  const second = { ...job('job-second', 'second prompt', 'running'), images: [], image_id: null, image_url: null };
  await installJobStreams(page, { events: { 'job-first': finished } });
  await loadApp(page, { generatedJobs: [finished, second] });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const generate = page.getByRole('button', { name: 'Generate', exact: true });
  await prompt.fill('first prompt');
  await generate.click();

  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/img-1.png');

  await prompt.fill('second prompt');
  await generate.click();

  // The main image is unchanged, and the new request shows up as a pending slot.
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/img-1.png');
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(1);
  await expect(preview.getByText('1 still generating')).toBeVisible();
});

test('two requests of n=2 reserve four slots in submission order', async ({ page }) => {
  const pending = { ...job('job-pending', 'four slots prompt', 'running'), images: [], image_id: null, image_url: null };
  await installJobStreams(page, { events: {} });
  await loadApp(page, { generatedJobs: [pending] });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const generate = page.getByRole('button', { name: 'Generate', exact: true });
  const bodies: number[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/generate') bodies.push(request.postDataJSON().n);
  });
  await prompt.fill('four slots prompt');
  await page.getByLabel('Quantity').fill('2');
  await generate.click();
  await generate.click();

  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(4);
  // Both POSTs carried the snapshot quantity.
  await expect.poll(() => bodies).toEqual([2, 2]);
});

test('a failed submission stops its own slot without removing another job\'s image', async ({ page }) => {
  const finished = job('job-first', 'kept prompt');
  await installJobStreams(page, { events: { 'job-first': finished } });
  await loadApp(page, { generatedJobs: [finished] });
  await page.route('**/api/generate', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON();
    if (body.prompt === 'failing prompt') {
      await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'upstream exploded' }) });
      return;
    }
    // Fall through to the fixture's own /api/generate handler.
    await route.fallback();
  });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const generate = page.getByRole('button', { name: 'Generate', exact: true });
  await prompt.fill('kept prompt');
  await generate.click();
  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  await prompt.fill('failing prompt');
  await generate.click();
  await expect(preview.locator('[data-slot-status="failed"]')).toHaveCount(1);
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toBeVisible();
});

test('clearing the preview stops its streams and does not revive them with late results', async ({ page }) => {
  const late = job('job-late', 'late prompt', 'success');
  await installJobStreams(page, { events: { 'job-late': late } });
  await loadApp(page, { generatedJobs: [late] });

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('late prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await preview.getByRole('button', { name: 'Clear', exact: true }).click();

  await expect(preview.getByText('No preview yet')).toBeVisible();
  await page.waitForTimeout(600);
  await expect(preview.getByText('No preview yet')).toBeVisible();
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toHaveCount(0);
  const state = await streamState(page);
  expect(state?.closed).toContain('/api/generate/job-late/events');
});

test('streaming frames stay with their own job when two requests run together', async ({ page }) => {
  const first = { ...job('job-alpha', 'alpha prompt', 'running'), n: 1, streaming: true, images: [], image_id: null, image_url: null };
  const second = { ...job('job-beta', 'beta prompt', 'running'), n: 1, streaming: true, images: [], image_id: null, image_url: null };
  await installJobStreams(page, { events: { 'job-alpha': first, 'job-beta': second } });
  await loadApp(page, { generatedJobs: [first, second] });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const generate = page.getByRole('button', { name: 'Generate', exact: true });
  await prompt.fill('alpha prompt');
  await generate.click();
  await prompt.fill('beta prompt');
  await generate.click();

  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(2);
  // Each POST resolves and opens its own stream; neither replaces the other.
  await expect
    .poll(async () => (await streamState(page))?.opened ?? [])
    .toEqual(
      expect.arrayContaining(['/api/generate/job-alpha/events', '/api/generate/job-beta/events'])
    );
  const state = await streamState(page);
  expect(state?.closed).not.toContain('/api/generate/job-alpha/events');
});

test('n=2 results keep their unit slots when the second unit finishes first', async ({ page }) => {
  const running = {
    ...job('job-pair', 'pair prompt', 'running'),
    n: 2,
    status: 'running',
    images: [],
    image_id: null,
    image_url: null,
    unit_statuses: { '0': 'running', '1': 'running' }
  };
  const unit = (index: number) => ({
    image_id: `pair-${index}`,
    image_url: `/api/image/pair-${index}.png`,
    filename: `pair-${index}.png`,
    unit_index: index - 1,
    result_index: 0,
    image_width: 1,
    image_height: 1
  });
  await installJobStreams(page, {
    sequenceIntervalMs: 1500,
    sequences: {
      'job-pair': [
        // Unit 1 completes first: it must land in slot 2, not slot 1.
        { ...running, images: [unit(2)], unit_statuses: { '0': 'running', '1': 'success' } },
        // Unit 0 completes later: slot 2 keeps its image and its selection.
        {
          ...job('job-pair', 'pair prompt'),
          n: 2,
          images: [unit(1), unit(2)],
          unit_statuses: { '0': 'success', '1': 'success' }
        }
      ]
    }
  });
  await loadApp(page, { generatedJob: running });

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('pair prompt');
  await page.getByLabel('Quantity').fill('2');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();

  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  // The second unit's image is the one on screen, and it is the second slot.
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/pair-2.png');
  await expect(preview.getByRole('button', { name: 'Select result 2' })).toHaveAttribute('aria-pressed', 'true');
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(1);

  // When the first unit lands, the strip shows both in unit order and the
  // main image does not jump back to slot 1.
  await expect(preview.getByRole('button', { name: 'Select result 1' })).toBeVisible();
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/pair-2.png');
  await expect(preview.getByRole('button', { name: 'Select result 2' })).toHaveAttribute('aria-pressed', 'true');
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(0);
});

test('a later-finishing submission does not steal the main image', async ({ page }) => {
  const first = { ...job('job-slow', 'slow prompt', 'running'), images: [], image_id: null, image_url: null };
  const second = { ...job('job-fast', 'fast prompt', 'running'), images: [], image_id: null, image_url: null };
  await installJobStreams(page, {
    // The second submission finishes well before the first one, even though
    // its stream opens later.
    sequenceStartMs: { 'job-fast': 300, 'job-slow': 2500 },
    sequences: {
      'job-fast': [
        {
          ...job('job-fast', 'fast prompt'),
          images: [{ image_id: 'fast-1', image_url: '/api/image/fast-1.png', filename: 'fast-1.png', unit_index: 0 }]
        }
      ],
      'job-slow': [
        {
          ...job('job-slow', 'slow prompt'),
          images: [{ image_id: 'slow-1', image_url: '/api/image/slow-1.png', filename: 'slow-1.png', unit_index: 0 }]
        }
      ]
    }
  });
  await loadApp(page, { generatedJobs: [first, second] });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const generate = page.getByRole('button', { name: 'Generate', exact: true });
  await prompt.fill('slow prompt');
  await generate.click();
  await prompt.fill('fast prompt');
  await generate.click();

  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  // The faster submission becomes the main image...
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/fast-1.png');
  // ...and stays there once the earlier submission completes, which now also
  // fills the strip's first slot.
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(0);
  await expect(preview.getByRole('button', { name: 'Select result 1' })).toBeVisible();
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/fast-1.png');
  await expect(preview.getByRole('button', { name: 'Select result 2' })).toHaveAttribute('aria-pressed', 'true');
});

test('a failed unit stops spinning while its siblings keep generating', async ({ page }) => {
  const running = {
    ...job('job-mixed', 'mixed prompt', 'running'),
    n: 3,
    status: 'running',
    images: [],
    image_id: null,
    image_url: null,
    unit_statuses: { '0': 'running', '1': 'running', '2': 'running' }
  };
  await installJobStreams(page, {
    sequenceIntervalMs: 1500,
    sequences: {
      'job-mixed': [
        // One success, one failure, one still generating.
        {
          ...running,
          images: [{ image_id: 'mixed-1', image_url: '/api/image/mixed-1.png', filename: 'mixed-1.png', unit_index: 0, result_index: 0 }],
          unit_statuses: { '0': 'success', '1': 'upstream_error', '2': 'running' }
        }
      ]
    }
  });
  await loadApp(page, { generatedJob: running });

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('mixed prompt');
  await page.getByLabel('Quantity').fill('3');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();

  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  // Exactly one unit is still generating; the failed one must not spin.
  await expect(preview.getByText('1 still generating')).toBeVisible();
  await expect(preview.locator('[data-slot-status="waiting"]')).toHaveCount(1);
  await expect(preview.locator('[data-slot-status="failed"]')).toHaveCount(1);
});
