import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '$lib/api/client';
import { initialPromptFormState, previewStore } from '$lib/stores/preview';
import { editSourceStore } from '$lib/stores/editSource';
import type { GenerateJobStatus } from '$lib/api/types/jobs';

vi.mock('$lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('$lib/api/client')>()),
  apiFetch: vi.fn()
}));

const apiFetchMock = vi.mocked(apiFetch);

const form = (prompt: string, quantity = 1) => ({ ...initialPromptFormState, prompt, quantity });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const response = (jobId: string) => ({ job_id: jobId, status: 'queued' as const, stage: 'queued' as const, operation: 'generation' as const });

function job(jobId: string, overrides: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return { job_id: jobId, status: 'running', n: 1, prompt: 'p', ...overrides };
}

beforeEach(() => {
  apiFetchMock.mockReset();
  previewStore.clearPreview();
});

describe('continuous generation submissions', () => {
  it('reserves slots synchronously and keeps three out-of-order responses in click order', async () => {
    const first = deferred<ReturnType<typeof response>>();
    const second = deferred<ReturnType<typeof response>>();
    const third = deferred<ReturnType<typeof response>>();
    apiFetchMock
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never)
      .mockReturnValueOnce(third.promise as never);

    const trackJob = vi.fn();
    const firstSubmit = previewStore.generateImage(form('first', 2), { trackJob });
    const secondSubmit = previewStore.generateImage(form('second', 2), { trackJob });
    const thirdSubmit = previewStore.generateImage(form('third', 2), { trackJob });

    // Three independent POSTs, each with its own snapshot.
    expect(apiFetchMock).toHaveBeenCalledTimes(3);
    expect(apiFetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body)).prompt)).toEqual(['first', 'second', 'third']);
    expect(get(previewStore).slots.map((slot) => slot.unitIndex)).toEqual([0, 1, 0, 1, 0, 1]);
    expect(get(previewStore).slots.every((slot) => slot.status === 'waiting')).toBe(true);

    third.resolve(response('job-c'));
    await thirdSubmit;
    second.resolve(response('job-b'));
    await secondSubmit;
    first.resolve(response('job-a'));
    await firstSubmit;

    expect(trackJob.mock.calls.map(([jobId]) => jobId)).toEqual(['job-c', 'job-b', 'job-a']);
    const slots = get(previewStore).slots;
    expect(slots.map((slot) => slot.jobId)).toEqual(['job-a', 'job-a', 'job-b', 'job-b', 'job-c', 'job-c']);
    expect(slots.map((slot) => slot.prompt)).toEqual(['first', 'first', 'second', 'second', 'third', 'third']);
  });

  it('keeps an existing final image selected while a new submission is pending', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first'), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      status: 'success',
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
    }));
    const selectedBefore = get(previewStore).selectedSlot?.id;
    expect(selectedBefore).toBeTruthy();

    apiFetchMock.mockReturnValueOnce(new Promise(() => {}) as never);
    void previewStore.generateImage(form('second'), { trackJob: () => {} });

    const view = get(previewStore);
    expect(view.hasFinalImage).toBe(true);
    expect(view.selectedSlot?.id).toBe(selectedBefore);
    expect(view.pendingSlots).toHaveLength(1);
    expect(view.selectedSlot?.prompt).toBe('first');
    expect(view.completedCount).toBe(1);
    expect(view.totalCount).toBe(2);
  });

  it('isolates a failed submission from another job that keeps streaming', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first', 2), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', { n: 2 }));

    apiFetchMock.mockRejectedValueOnce(new Error('rejected by upstream') as never);
    const failed = await previewStore.generateImage(form('second'), { trackJob: () => {} });
    expect(failed).toBe(false);

    previewStore.applyJob(job('job-a', {
      n: 2,
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }],
      unit_statuses: { '0': 'success', '1': 'running' }
    }));

    const view = get(previewStore);
    expect(view.completedCount).toBe(1);
    expect(view.slots.filter((slot) => slot.status === 'failed')).toHaveLength(1);
    // The first request reserved two slots; the second is still running.
    expect(view.slots.filter((slot) => slot.status === 'waiting')).toHaveLength(1);
    expect(view.selectedSlot?.image?.image_id).toBe('a-1');
  });

  it('never attaches a late POST response to a replacement group', async () => {
    const slow = deferred<ReturnType<typeof response>>();
    apiFetchMock.mockReturnValueOnce(slow.promise as never);
    const trackJob = vi.fn();
    const submit = previewStore.generateImage(form('stale'), { trackJob });
    previewStore.clearPreview();
    slow.resolve(response('job-late'));
    expect(await submit).toBe(true);
    // The backend job is real, but it must not appear in the new group.
    expect(trackJob).not.toHaveBeenCalled();
    expect(get(previewStore).slots).toEqual([]);
  });

  it('turns terminal jobs into explicit slot states instead of spinning forever', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first', 3), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      status: 'partial_failure',
      n: 3,
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }],
      unit_statuses: { '0': 'success', '1': 'upstream_error', '2': 'cancelled' }
    }));
    expect(get(previewStore).slots.map((slot) => slot.status)).toEqual(['success', 'failed', 'cancelled']);
    expect(get(previewStore).pendingSlots).toEqual([]);
  });

  it('stops a failed unit while the parent job is still running', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first', 3), { trackJob: () => {} });
    // n=3: success, failure, still generating. The failed unit must not keep
    // spinning just because the parent job is running.
    previewStore.applyJob(job('job-a', {
      status: 'running',
      n: 3,
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }],
      unit_statuses: { '0': 'success', '1': 'upstream_error', '2': 'running' }
    }));
    const view = get(previewStore);
    expect(view.slots.map((slot) => slot.status)).toEqual(['success', 'failed', 'waiting']);
    expect(view.pendingSlots.map((slot) => slot.unitIndex)).toEqual([2]);
    expect(view.pendingCount).toBe(1);

    // A cancelled unit behaves the same way.
    previewStore.applyJob(job('job-a', {
      status: 'running',
      n: 3,
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }],
      unit_statuses: { '0': 'success', '1': 'cancelled', '2': 'running' }
    }));
    expect(get(previewStore).slots.map((slot) => slot.status)).toEqual(['success', 'cancelled', 'waiting']);
    expect(get(previewStore).pendingCount).toBe(1);
  });

  it('keeps results the backend produced beyond the reserved count reachable', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first', 1), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      status: 'success',
      n: 1,
      images: [
        { image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 },
        { image_id: 'a-2', image_url: '/api/image/a-2.png', filename: 'a-2.png', unit_index: 1 }
      ]
    }));
    expect(get(previewStore).completedSlots.map((slot) => slot.image?.image_id)).toEqual(['a-1', 'a-2']);
  });

  it('keeps selection stable when a later image arrives', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first', 2), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      n: 2,
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
    }));
    const firstSlot = get(previewStore).selectedSlot!;
    previewStore.selectSlot(firstSlot.id);
    previewStore.applyJob(job('job-a', {
      n: 2,
      status: 'success',
      images: [
        { image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 },
        { image_id: 'a-2', image_url: '/api/image/a-2.png', filename: 'a-2.png', unit_index: 1 }
      ]
    }));
    expect(get(previewStore).selectedSlot?.image?.image_id).toBe('a-1');
  });

  it('keeps each n=2 result in its own unit slot when the second unit finishes first', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('pair', 2), { trackJob: () => {} });

    // Unit 1 completes first: it must occupy slot 2, not the first free slot.
    previewStore.applyJob(job('job-a', {
      n: 2,
      images: [{ image_id: 'a-2', image_url: '/api/image/a-2.png', filename: 'a-2.png', unit_index: 1, result_index: 0 }],
      unit_statuses: { '0': 'running', '1': 'success' }
    }));
    let slots = get(previewStore).slots;
    expect(slots.map((slot) => slot.unitIndex)).toEqual([0, 1]);
    expect(slots.map((slot) => slot.image?.image_id ?? null)).toEqual([null, 'a-2']);
    expect(slots.map((slot) => slot.status)).toEqual(['waiting', 'success']);
    const secondUnitSlotId = slots[1].id;
    expect(get(previewStore).selectedSlot?.id).toBe(secondUnitSlotId);

    // Unit 0 completes later: slot 1 keeps its image and stays selected.
    previewStore.applyJob(job('job-a', {
      n: 2,
      status: 'success',
      images: [
        { image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0, result_index: 0 },
        { image_id: 'a-2', image_url: '/api/image/a-2.png', filename: 'a-2.png', unit_index: 1, result_index: 0 }
      ],
      unit_statuses: { '0': 'success', '1': 'success' }
    }));
    slots = get(previewStore).slots;
    expect(slots.map((slot) => slot.image?.image_id)).toEqual(['a-1', 'a-2']);
    expect(slots[1].id).toBe(secondUnitSlotId);
    expect(get(previewStore).selectedSlot?.image?.image_id).toBe('a-2');
  });

  it('identifies result slots by result_index, not response array position', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('pair', 2), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      n: 2,
      status: 'success',
      images: [
        { image_id: 'a-2', image_url: '/api/image/a-2.png', filename: 'a-2.png', unit_index: 1, result_index: 0 },
        { image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0, result_index: 0 }
      ]
    }));
    const slots = get(previewStore).slots;
    // Derived order is unit order, independent of the response array order.
    expect(slots.map((slot) => slot.unitIndex)).toEqual([0, 1]);
    expect(slots.map((slot) => slot.image?.image_id)).toEqual(['a-1', 'a-2']);
    expect(slots.map((slot) => slot.resultIndex)).toEqual([0, 0]);
    expect(new Set(slots.map((slot) => slot.id)).size).toBe(2);
  });

  it('keeps the first automatic selection when an earlier submission finishes later', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first'), { trackJob: () => {} });
    apiFetchMock.mockResolvedValueOnce(response('job-b') as never);
    await previewStore.generateImage(form('second'), { trackJob: () => {} });

    // B finishes first and becomes the main image.
    previewStore.applyJob(job('job-b', {
      status: 'success',
      prompt: 'second',
      images: [{ image_id: 'b-1', image_url: '/api/image/b-1.png', filename: 'b-1.png', unit_index: 0 }]
    }));
    expect(get(previewStore).selectedSlot?.image?.image_id).toBe('b-1');

    // A finishes later; the main image must not jump to it.
    previewStore.applyJob(job('job-a', {
      status: 'success',
      prompt: 'first',
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
    }));
    const view = get(previewStore);
    expect(view.selectedSlot?.image?.image_id).toBe('b-1');
    expect(view.prompt).toBe('second');
    expect(view.completedSlots.map((slot) => slot.image?.image_id)).toEqual(['a-1', 'b-1']);
  });

  it('falls back to the first completed slot when the selected slot disappears', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first'), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      status: 'success',
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
    }));
    previewStore.applyJob(job('job-a', {
      status: 'success',
      images: [{ image_id: 'a-1b', image_url: '/api/image/a-1b.png', filename: 'a-1b.png', unit_index: 0, result_index: 0 }]
    }));
    expect(get(previewStore).selectedSlot?.image?.image_id).toBe('a-1b');
  });

  it('reports metadata from the selected image\'s own job', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first'), { trackJob: () => {} });
    apiFetchMock.mockResolvedValueOnce(response('job-b') as never);
    await previewStore.generateImage(form('second'), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      status: 'success',
      model: 'gpt-image-2',
      prompt: 'first',
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
    }));
    previewStore.applyJob(job('job-b', { model: 'gpt-image-1' }));
    const view = get(previewStore);
    expect(view.selectedSlot?.job?.job_id).toBe('job-a');
    expect(view.job?.model).toBe('gpt-image-2');
    expect(view.prompt).toBe('first');
  });

  it('rejects an invalid form without touching the current group', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first'), { trackJob: () => {} });
    previewStore.applyJob(job('job-a', {
      status: 'success',
      images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
    }));
    apiFetchMock.mockClear();
    const ok = await previewStore.generateImage(form('   '), { trackJob: () => {} });
    expect(ok).toBe(false);
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(get(previewStore).error).toBeTruthy();
    expect(get(previewStore).completedSlots).toHaveLength(1);
  });

  it('starts a fresh group when generating after an edit result', async () => {
    editSourceStore.addFiles([new File([new Uint8Array([1])], 'source.png', { type: 'image/png' })], () => {});
    apiFetchMock.mockResolvedValueOnce({ ...response('job-edit'), operation: 'edit' } as never);
    await previewStore.editImage(
      form('edit prompt'),
      get(editSourceStore),
      { trackJob: () => {} }
    );
    previewStore.applyJob(job('job-edit', {
      status: 'success',
      operation: 'edit',
      images: [{ image_id: 'e-1', image_url: '/api/image/e-1.png', filename: 'e-1.png', unit_index: 0 }]
    }));
    expect(get(previewStore).completedSlots).toHaveLength(1);

    apiFetchMock.mockResolvedValueOnce(response('job-gen') as never);
    await previewStore.generateImage(form('generation prompt'), { trackJob: () => {} });
    const view = get(previewStore);
    expect(view.slots).toHaveLength(1);
    expect(view.slots[0].jobId).toBe('job-gen');
    expect(view.compareSource).toBeNull();
  });

  it('opens a history job as its own group', async () => {
    apiFetchMock.mockResolvedValueOnce(response('job-a') as never);
    await previewStore.generateImage(form('first'), { trackJob: () => {} });
    previewStore.openJob(job('job-history', {
      status: 'success',
      prompt: 'history prompt',
      images: [{ image_id: 'h-1', image_url: '/api/image/h-1.png', filename: 'h-1.png', unit_index: 0 }]
    }));
    const view = get(previewStore);
    expect(view.slots).toHaveLength(1);
    expect(view.selectedSlot?.jobId).toBe('job-history');
    expect(view.prompt).toBe('history prompt');
  });
});
