import { File } from 'node:buffer';
import { beforeEach, expect, it, vi } from 'vitest';
import type { GalleryActionDeps } from '$lib/stores/galleryDeferredActions';

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn(), wait: vi.fn(), loadCollections: vi.fn() }));
vi.mock('$lib/api/client', () => ({ apiFetch: mocks.apiFetch, apiFetchRaw: vi.fn() }));
vi.mock('$lib/stores/galleryJobEvents', () => ({ waitForGalleryJob: mocks.wait, abortError: () => new Error('aborted') }));
vi.mock('$lib/stores/galleryCollections', () => ({ galleryCollectionsStore: { load: mocks.loadCollections } }));
import { createDeferredGalleryActions } from '$lib/stores/galleryDeferredActions';

beforeEach(() => {
  vi.resetAllMocks();
  mocks.apiFetch.mockResolvedValue({ job_id: 'job' });
  mocks.wait.mockResolvedValue({ status: 'success', imported_count: 1 });
  mocks.loadCollections.mockResolvedValue(undefined);
});

function setup() {
  const loadGallery = vi.fn().mockResolvedValue(undefined);
  const toast = vi.fn();
  const deps = { loadGallery, setOperationStatus: vi.fn(), registerAbortController: () => () => {} } as unknown as GalleryActionDeps;
  return { actions: createDeferredGalleryActions(deps), loadGallery, toast };
}
const archive = () => new File(['zip'], 'archive.zip') as unknown as globalThis.File;

it('refreshes collections after successful import before refreshing gallery', async () => {
  const { actions, loadGallery, toast } = setup();
  await actions.importArchive(archive(), toast);
  expect(mocks.loadCollections).toHaveBeenCalledWith(true);
  expect(mocks.loadCollections.mock.invocationCallOrder[0]).toBeLessThan(loadGallery.mock.invocationCallOrder[0]);
  expect(toast).toHaveBeenCalledOnce();
});

it('does not refresh after a failed import job', async () => {
  const { actions, loadGallery, toast } = setup();
  mocks.wait.mockRejectedValue(new Error('import failed'));
  await expect(actions.importArchive(archive(), toast)).rejects.toThrow('import failed');
  expect(mocks.loadCollections).not.toHaveBeenCalled();
  expect(loadGallery).not.toHaveBeenCalled();
  expect(toast).not.toHaveBeenCalled();
});

it('reports a successful import even if collections refresh fails', async () => {
  const { actions, loadGallery, toast } = setup();
  mocks.loadCollections.mockRejectedValue(new Error('offline'));
  await actions.importArchive(archive(), toast);
  expect(loadGallery).toHaveBeenCalledOnce();
  expect(toast).toHaveBeenCalledOnce();
});
