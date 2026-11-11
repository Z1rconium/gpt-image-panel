import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';
import { previewStore } from '$lib/stores/preview';
import type { GeneratePreviewEvent, GenerateJobStatus } from '$lib/api/types/jobs';

function previewEvent(overrides: Partial<GeneratePreviewEvent>): GeneratePreviewEvent {
  return {
    job_id: 'job-1',
    unit_index: 0,
    partial_image_index: 0,
    sequence: 1,
    mime_type: 'image/png',
    data_url: 'data:image/png;base64,AAAA',
    ...overrides
  };
}

function runningJob(overrides: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return { job_id: 'job-1', status: 'running', n: 2, streaming: true, prompt: 'test', ...overrides };
}

function openRunning(job = runningJob()) {
  previewStore.openJob(job);
  return get(previewStore);
}

describe('preview store streaming slots', () => {
  it('keeps unfinished units visible as other units complete and rejects late finals', () => {
    openRunning();
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, data_url: 'data:first' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, data_url: 'data:second' }));
    const running = runningJob({
      images: [{ image_id: 'first', image_url: '/api/image/first.png', filename: 'first.png', unit_index: 0 }],
      unit_statuses: { '0': 'success', '1': 'running' }
    });
    previewStore.applyJob(running);
    let slots = get(previewStore).slots;
    expect(slots.filter((slot) => slot.status === 'streaming').map((slot) => slot.unitIndex)).toEqual([1]);
    expect(slots.find((slot) => slot.unitIndex === 0)?.status).toBe('success');

    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 99, data_url: 'data:late' }));
    slots = get(previewStore).slots;
    expect(slots.find((slot) => slot.unitIndex === 0)?.image?.image_id).toBe('first');
    expect(slots.filter((slot) => slot.status === 'streaming')).toHaveLength(1);

    previewStore.applyJob({ ...running, status: 'partial_failure', unit_statuses: { '0': 'success', '1': 'upstream_error' } });
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, sequence: 99 }));
    expect(get(previewStore).pendingSlots).toEqual([]);
    expect(get(previewStore).slots.map((slot) => slot.status)).toEqual(['success', 'failed']);
  });

  it('keeps simultaneous Responses calls distinct and bounds incoming frames', () => {
    openRunning();
    previewStore.applyPreviewEvent(previewEvent({ call_index: 1, data_url: 'data:call-a' }));
    previewStore.applyPreviewEvent(previewEvent({ call_index: 2, data_url: 'data:call-b' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 11 }));
    previewStore.applyPreviewEvent(previewEvent({ call_index: 11 }));
    expect(get(previewStore).slots.filter((slot) => slot.status === 'streaming')).toHaveLength(2);
    previewStore.setError('cancelled');
    expect(get(previewStore).error).toBe('cancelled');
  });

  it('buckets preview frames per unit index and drops stale sequences', () => {
    openRunning();
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 1, data_url: 'data:unit0-1' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, sequence: 1, data_url: 'data:unit1-1' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 0, data_url: 'data:unit0-stale' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 2, data_url: 'data:unit0-2' }));

    const slots = get(previewStore).slots;
    expect(slots.map((slot) => slot.previewUrl)).toEqual(['data:unit0-2', 'data:unit1-1']);
  });

  it('ignores frames for other jobs and once the final image landed', () => {
    openRunning();
    previewStore.applyPreviewEvent(previewEvent({ job_id: 'other-job', data_url: 'data:other' }));
    expect(get(previewStore).pendingSlots.every((slot) => !slot.previewUrl)).toBe(true);

    previewStore.openJob({
      job_id: 'job-final',
      status: 'success',
      n: 1,
      image_id: 'final',
      image_url: '/api/image/final.png'
    });
    previewStore.applyPreviewEvent(previewEvent({ job_id: 'job-final', unit_index: 0, sequence: 5, data_url: 'data:late' }));
    expect(get(previewStore).completedSlots).toHaveLength(1);
    expect(get(previewStore).pendingSlots).toEqual([]);
  });

  it('shows a legacy single-image job instead of leaving its slot waiting', () => {
    previewStore.openJob(runningJob({ n: 1, streaming: false }));
    previewStore.applyJob({
      job_id: 'job-1',
      status: 'success',
      n: 1,
      prompt: 'test',
      image_id: 'legacy',
      image_url: '/api/image/legacy.png'
    });
    const view = get(previewStore);
    expect(view.completedCount).toBe(1);
    expect(view.imageUrl).toBe('/api/image/legacy.png');
    expect(view.slots.map((slot) => [slot.unitIndex, slot.resultIndex, slot.status])).toEqual([[0, 0, 'success']]);
  });
});
