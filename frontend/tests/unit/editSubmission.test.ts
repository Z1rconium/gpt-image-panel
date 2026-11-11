import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '$lib/api/client';
import {
  editSourceStore,
  initialEditSourceState,
  type EditGalleryReferenceSource
} from '$lib/stores/editSource';
import { initialPromptFormState, previewStore } from '$lib/stores/preview';

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

async function submit(form: Partial<typeof initialPromptFormState> = {}) {
  const ok = await previewStore.editImage(
    { ...initialPromptFormState, prompt: 'make it blue', ...form },
    get(editSourceStore),
    { trackJob: () => {} }
  );
  const [endpoint, init] = apiFetchMock.mock.calls[0] ?? [];
  const entries = init?.body instanceof FormData ? [...init.body.entries()] : [];
  const sourceFields = entries
    .filter(([key]) => key === 'image' || key === 'image[]' || key === 'reference_image_id')
    .map(([key, value]) => [key, value instanceof File ? value.name : value]);
  return { ok, endpoint, sourceFields, entries };
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

describe('multi-image edit preview', () => {
  it('reserves every requested unit and keeps streamed frames past the first image', async () => {
    editSourceStore.addFiles([makeFile('source.png')], () => {});
    apiFetchMock.mockResolvedValueOnce({ job_id: 'job-edit', status: 'queued', operation: 'edit' } as never);
    const { ok, entries } = await submit({ quantity: 2 });
    expect(ok).toBe(true);
    // The backend still accepts n>1 for edits, so the request carries it.
    expect(entries).toContainEqual(['n', '2']);
    expect(get(previewStore).slots).toHaveLength(2);

    previewStore.applyJob({
      job_id: 'job-edit',
      status: 'running',
      operation: 'edit',
      n: 2,
      streaming: true,
      unit_statuses: { '0': 'running', '1': 'running' }
    });
    const frame = (unitIndex: number, sequence: number) => ({
      job_id: 'job-edit',
      unit_index: unitIndex,
      partial_image_index: 0,
      sequence,
      mime_type: 'image/png',
      data_url: `data:image/png;base64,unit-${unitIndex}`
    });
    previewStore.applyPreviewEvent(frame(0, 1));
    previewStore.applyPreviewEvent(frame(1, 1));
    expect(get(previewStore).slots.map((slot) => slot.previewUrl)).toEqual([
      'data:image/png;base64,unit-0',
      'data:image/png;base64,unit-1'
    ]);

    // The first image landing must not end the loading state for the second.
    previewStore.applyJob({
      job_id: 'job-edit',
      status: 'running',
      operation: 'edit',
      n: 2,
      images: [{ image_id: 'e-1', image_url: '/api/image/e-1.png', filename: 'e-1.png', unit_index: 0, result_index: 0 }],
      unit_statuses: { '0': 'success', '1': 'running' }
    });
    const view = get(previewStore);
    expect(view.loading).toBe(true);
    expect(view.pendingSlots).toHaveLength(1);
    expect(view.pendingSlots[0].unitIndex).toBe(1);
    expect(view.completedCount).toBe(1);
  });
});

describe('edit tracking ownership', () => {
  it('releases only the jobs it replaces, so a running generation keeps streaming', async () => {
    const trackJob = vi.fn();
    const releaseJobs = vi.fn();
    const releaseJob = vi.fn();
    apiFetchMock.mockResolvedValueOnce({ job_id: 'job-gen', status: 'queued', stage: 'queued', operation: 'generation' } as never);
    await previewStore.generateImage(
      { ...initialPromptFormState, prompt: 'generate first' },
      { trackJob, releaseJobs, releaseJob }
    );
    expect(trackJob).toHaveBeenCalledWith('job-gen');
    // The previous test left its edit result in the group; the first generation
    // after an edit starts a fresh group and releases every tracked job.
    expect(releaseJobs).toHaveBeenCalledTimes(1);
    releaseJobs.mockClear();

    editSourceStore.addFiles([makeFile('source.png')], () => {});
    apiFetchMock.mockResolvedValueOnce({ job_id: 'job-edit', status: 'queued', stage: 'queued', operation: 'edit' } as never);
    await previewStore.editImage(
      { ...initialPromptFormState, prompt: 'make it blue' },
      get(editSourceStore),
      { trackJob, releaseJobs, releaseJob }
    );

    expect(releaseJob.mock.calls).toEqual([['job-gen']]);
    expect(releaseJobs.mock.calls.length).toBe(0);
    expect(trackJob).toHaveBeenLastCalledWith('job-edit');
  });
});
