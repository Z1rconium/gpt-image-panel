import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '$lib/api/client';
import {
  editSourceStore,
  initialEditSourceState,
  type EditGalleryReferenceSource
} from '$lib/stores/editSource';
import { initialPromptFormState, previewStore, type PreviewState } from '$lib/stores/preview';

vi.mock('$lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('$lib/api/client')>()),
  apiFetch: vi.fn()
}));

const apiFetchMock = vi.mocked(apiFetch);

const makeFile = (name: string) => new File([new Uint8Array([1])], name, { type: 'image/png' });

const reference = (id: string): EditGalleryReferenceSource => ({
  id: `gallery-ref-${id}`,
  imageId: id,
  filename: `${id}.png`,
  label: id,
  previewUrl: `blob:${id}`,
  previewLabel: id,
  width: 10,
  height: 10
});

const queued = (prompt: string): PreviewState => ({
  loading: true,
  error: '',
  job: null,
  imageUrl: '',
  filename: '',
  prompt,
  streamingPreviews: {},
  compareSource: null
});

async function submit() {
  const ok = await previewStore.editImage(
    { ...initialPromptFormState, prompt: 'make it blue' },
    get(editSourceStore),
    queued,
    () => {}
  );
  const [endpoint, init] = apiFetchMock.mock.calls[0] ?? [];
  const entries = init?.body instanceof FormData ? [...init.body.entries()] : [];
  const sourceFields = entries
    .filter(([key]) => key === 'image' || key === 'image[]' || key === 'reference_image_id')
    .map(([key, value]) => [key, value instanceof File ? value.name : value]);
  return { ok, endpoint, sourceFields };
}

beforeEach(() => {
  let counter = 0;
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => `blob:test-${(counter += 1)}`;
  (URL as unknown as { revokeObjectURL: (url: string) => void }).revokeObjectURL = () => {};
  editSourceStore.set({ ...initialEditSourceState });
  apiFetchMock.mockReset();
  apiFetchMock.mockResolvedValue({ job_id: 'job-1' } as never);
});

describe('edit submission request shape', () => {
  it('sends the gallery primary as the endpoint, then uploads, then reference ids', async () => {
    // Added deliberately out of canonical order.
    editSourceStore.addGalleryReferences([reference('ref-1')]);
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    editSourceStore.addGalleryReferences([reference('ref-2')]);
    editSourceStore.setGallerySource('gal 1', 'Gal', 'blob:gal', 'gal');
    editSourceStore.addFiles([makeFile('b.png')], () => {});

    const { ok, endpoint, sourceFields } = await submit();
    expect(ok).toBe(true);
    expect(endpoint).toBe('/api/edits/from-gallery/gal%201');
    expect(sourceFields).toEqual([
      ['image[]', 'a.png'],
      ['image[]', 'b.png'],
      ['reference_image_id', 'ref-1'],
      ['reference_image_id', 'ref-2']
    ]);
  });

  it('uses the plain edits endpoint and a single `image` field for one upload', async () => {
    editSourceStore.addFiles([makeFile('only.png')], () => {});
    const { ok, endpoint, sourceFields } = await submit();
    expect(ok).toBe(true);
    expect(endpoint).toBe('/api/edits');
    expect(sourceFields).toEqual([['image', 'only.png']]);
  });

  it('submits reference-only edits to the plain endpoint without upload fields', async () => {
    editSourceStore.addGalleryReferences([reference('ref-1'), reference('ref-2')]);
    const { ok, endpoint, sourceFields } = await submit();
    expect(ok).toBe(true);
    expect(endpoint).toBe('/api/edits');
    expect(sourceFields).toEqual([
      ['reference_image_id', 'ref-1'],
      ['reference_image_id', 'ref-2']
    ]);
  });

  it('rejects markup that is not bound to a gallery primary or an upload', async () => {
    editSourceStore.addGalleryReferences([reference('ref-1')]);
    editSourceStore.setMarkup({ sourceId: 'gallery-ref-ref-1', blob: makeFile('m.png'), width: 8, height: 8, previewUrl: 'blob:m' });
    const { ok } = await submit();
    expect(ok).toBe(false);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('refuses to submit with no source at all', async () => {
    const { ok } = await submit();
    expect(ok).toBe(false);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
