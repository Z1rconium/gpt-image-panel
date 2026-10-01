import { expect, test } from '@playwright/test';
import { baseGalleryImages, job, loadApp } from './fixtures/mockApi';

test('green screen submits a local-removal mode with PNG output', async ({ page }) => {
  await loadApp(page);
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('green-screen flower');
  await page.getByLabel('Background').selectOption('chroma_green');
  const request = page.waitForRequest((candidate) => new URL(candidate.url()).pathname === '/api/generate' && candidate.method() === 'POST');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const body = (await request).postDataJSON();
  expect(body.background).toBe('chroma_green');
  expect(body.output_format).toBe('png');
  expect(body.prompt).toBe('green-screen flower');
});

test('preview switches per-image reported fields and prompt comparison', async ({ page }) => {
  const generatedJob = {
    ...job('job-generated', 'original flower'),
    size: 'auto', quality: 'auto',
    images: [
      { image_id: 'img-1', image_url: '/api/image/img-1.png', filename: 'img-1.png', image_width: 1024, image_height: 1024, revised_prompt: 'first rewrite', reported_size: '1024x1024', reported_quality: 'high', upstream_duration_ms: 123 },
      { image_id: 'img-2', image_url: '/api/image/img-2.png', filename: 'img-2.png', image_width: 1536, image_height: 1024, chroma_status: 'not_detected' }
    ]
  };
  await loadApp(page, { generatedJob });
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('original flower');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const trace = page.getByRole('region', { name: 'Image generation details' });
  await expect(trace).toContainText('first rewrite');
  await expect(trace).toContainText('123 ms');
  await expect(trace).toContainText('API reported size');
  await page.getByRole('button', { name: 'Select result 2' }).click();
  await expect(trace).toContainText('1536 × 1024');
  await expect(trace).toContainText('Not provided');
  await expect(trace).toContainText('Original image was kept');
  await expect(trace).not.toContainText('first rewrite');
});

test('history selects image details and gallery lightbox retains trace', async ({ page }) => {
  const historyJob = {
    ...job('history-trace', 'history flower'),
    images: [
      { image_id: 'img-1', image_url: '/api/image/img-1.png', filename: 'img-1.png', reported_quality: 'high' },
      { image_id: 'img-2', image_url: '/api/image/img-2.png', filename: 'img-2.png', reported_quality: 'medium', chroma_status: 'not_detected' }
    ]
  };
  await loadApp(page, {
    historyJobs: [historyJob],
    galleryImages: [{ ...baseGalleryImages[0], revised_prompt: 'gallery rewrite', sent_prompt: 'gallery sent prompt', reported_size: '1024x1024', upstream_duration_ms: 89 }]
  });
  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  const article = drawer.locator('article').filter({ hasText: 'history flower' });
  await expect(article.getByRole('region', { name: 'Image generation details' })).toContainText('high');
  await article.getByRole('button', { name: 'Image 2' }).click();
  await expect(article.getByRole('region', { name: 'Image generation details' })).toContainText('medium');
  await expect(article).toContainText('Original image was kept');
  await drawer.getByRole('button', { name: 'Close' }).click();
  await page.getByRole('img', { name: 'First gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox.getByRole('region', { name: 'Image generation details' })).toContainText('gallery rewrite');
  await expect(lightbox.getByRole('region', { name: 'Image generation details' })).toContainText('89 ms');
});

test('diagnostics explain rewritten prompts and ignored parameters', async ({ page }) => {
  const generatedJob = {
    ...job('job-generated', 'plain fox'),
    size: '1024x1536',
    api_preset_name: 'Gateway',
    images: [
      {
        image_id: 'img-1', image_url: '/api/image/img-1.png', filename: 'img-1.png', image_width: 1024, image_height: 1024,
        revised_prompt: 'an elaborate fox portrait', diagnostics: ['prompt_rewritten', 'size_ignored']
      }
    ]
  };
  await loadApp(page, { generatedJob, historyJobs: [generatedJob] });
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('plain fox');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const hints = page.getByRole('list', { name: 'Compatibility hints' }).first();
  await expect(hints).toContainText('Enable "Prevent prompt rewriting" on preset "Gateway"');
  await expect(hints).toContainText('ignored the requested size');

  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  await expect(drawer.locator('article').filter({ hasText: 'plain fox' })).toContainText('Upstream diverged from the request');
});

test('history marks reported parameter differences and opens unit diagnostics', async ({ page }) => {
  const failedJob = {
    ...job('diag-job', 'diag flower'),
    status: 'upstream_error' as const,
    error: 'Provider response did not include a task id at $.id',
    size: '1024x1024',
    quality: 'high',
    images: [
      {
        image_id: 'img-1',
        image_url: '/api/image/img-1.png',
        filename: 'img-1.png',
        reported_size: '1536x1024',
        reported_quality: 'high'
      }
    ]
  };
  await loadApp(page, { historyJobs: [failedJob] });
  await page.getByRole('button', { name: 'Job History' }).click();
  const drawer = page.getByRole('dialog', { name: 'Job History' });
  await drawer.getByRole('button', { name: 'History', exact: true }).click();
  const article = drawer.locator('article').filter({ hasText: 'diag flower' });
  await expect(article).toContainText('Differs from request');
  await expect(article).toContainText('Matches request');

  const diagnosticsRequest = page.waitForRequest((candidate) =>
    new URL(candidate.url()).pathname.endsWith('/diagnostics')
  );
  await article.getByRole('button', { name: 'Diagnostics', exact: true }).click();
  await diagnosticsRequest;
  await expect(article).toContainText('task_id_missing');
  await expect(article).toContainText('Provider response did not include a task id at $.id');
  await expect(article.getByRole('button', { name: 'Copy diagnostics' })).toBeVisible();
});
