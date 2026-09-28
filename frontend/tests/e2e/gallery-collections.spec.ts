import { expect, test } from '@playwright/test';
import { loadApp, type CollectionFixture } from './fixtures/mockApi';

const baseCollections: CollectionFixture[] = [
  { id: 'col-a', name: 'Portraits', position: 0, is_default: false, created_at: '2026-05-18T12:00:00Z', updated_at: '2026-05-18T12:00:00Z' },
  { id: 'col-b', name: 'Landscapes', position: 1, is_default: true, created_at: '2026-05-18T12:00:00Z', updated_at: '2026-05-18T12:00:00Z' }
];

function collectionNames(dialog: import('@playwright/test').Locator) {
  return dialog.locator('li[data-collection-row]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-collection-row')));
}

test('collections can be created, reordered by drag or buttons, and marked default', async ({ page }) => {
  await loadApp(page, { collections: baseCollections });

  await page.getByRole('button', { name: 'Collections', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Manage collections' });
  await expect(dialog).toBeVisible();
  await expect.poll(() => collectionNames(dialog)).toEqual(['Portraits', 'Landscapes']);

  await dialog.getByLabel('New collection name').fill('Moodboard');
  await dialog.getByRole('button', { name: 'Create', exact: true }).click();
  await expect.poll(() => collectionNames(dialog)).toEqual(['Portraits', 'Landscapes', 'Moodboard']);

  const reorderByButton = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/gallery/collections/order' && request.method() === 'PUT'
  );
  await dialog.getByRole('button', { name: 'Move Moodboard up' }).click();
  expect((await reorderByButton).postDataJSON()).toEqual({ ids: ['col-a', 'col-3', 'col-b'] });
  await expect.poll(() => collectionNames(dialog)).toEqual(['Portraits', 'Moodboard', 'Landscapes']);

  const reorderByDrag = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/gallery/collections/order' && request.method() === 'PUT'
  );
  await dialog.locator('li[data-collection-row="Landscapes"]').dragTo(dialog.locator('li[data-collection-row="Portraits"]'));
  expect((await reorderByDrag).postDataJSON()).toEqual({ ids: ['col-b', 'col-a', 'col-3'] });
  await expect.poll(() => collectionNames(dialog)).toEqual(['Landscapes', 'Portraits', 'Moodboard']);

  const portraitsRow = dialog.locator('li[data-collection-row="Portraits"]');
  await portraitsRow.getByRole('radio', { name: 'Default' }).check();
  await expect(portraitsRow.getByRole('radio', { name: 'Default' })).toBeChecked();
  await expect(dialog.locator('li[data-collection-row="Landscapes"]').getByRole('radio', { name: 'Default' })).not.toBeChecked();
});

test('images can be added to collections, filtered, removed and zipped per collection', async ({ page }) => {
  await loadApp(page, { collections: baseCollections, collectionItems: { 'col-a': ['img-2'] } });

  const firstCard = page.locator('.gallery-card').filter({ hasText: 'First gallery image' });
  const quickAdd = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/gallery/collections/col-b/items' && request.method() === 'POST'
  );
  await firstCard.getByRole('button', { name: 'Add to Landscapes' }).click();
  expect((await quickAdd).postDataJSON()).toEqual({ ids: ['img-1'] });
  await expect(page.getByRole('status')).toContainText('Added 1 image to Landscapes');

  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByLabel('Add to collection…').selectOption('col-a');
  await expect(page.getByRole('status')).toContainText('Added 1 image to Portraits');
  await page.getByRole('button', { name: 'Cancel selection' }).click();

  const filtered = page.waitForRequest((request) => {
    if (new URL(request.url()).pathname !== '/api/gallery/search') return false;
    return (request.postDataJSON() as { collection_id?: string }).collection_id === 'col-b';
  });
  await page.getByLabel('Collection', { exact: true }).selectOption('col-b');
  await filtered;
  await expect(page.locator('.gallery-card')).toHaveCount(1);
  await expect(page).toHaveURL(/collection=col-b/);

  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Remove from this collection' }).click();
  await expect(page.getByRole('status')).toContainText('Removed 1 image from Landscapes');
  await expect(page.locator('.gallery-card')).toHaveCount(0);

  await page.getByRole('button', { name: 'Collections', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Manage collections' });
  const exportRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/gallery/export-jobs' && request.method() === 'POST'
  );
  const download = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download Portraits as ZIP' }).click();
  expect((await exportRequest).postDataJSON()).toEqual({ collection_id: 'col-a' });
  expect((await download).suggestedFilename()).toBe('gpt-images-export.zip');
});

test('lightbox chips toggle collection membership', async ({ page }) => {
  await loadApp(page, { collections: baseCollections, collectionItems: { 'col-a': ['img-1'] } });

  await page.getByRole('img', { name: 'First gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  const portraits = lightbox.getByRole('button', { name: 'Portraits', exact: true });
  const landscapes = lightbox.getByRole('button', { name: 'Landscapes', exact: true });
  await expect(portraits).toHaveAttribute('aria-pressed', 'true');
  await expect(landscapes).toHaveAttribute('aria-pressed', 'false');

  const removeRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/gallery/collections/col-a/items/remove'
  );
  await portraits.click();
  expect((await removeRequest).postDataJSON()).toEqual({ ids: ['img-1'] });
  await expect(portraits).toHaveAttribute('aria-pressed', 'false');

  await landscapes.click();
  await expect(landscapes).toHaveAttribute('aria-pressed', 'true');
});
