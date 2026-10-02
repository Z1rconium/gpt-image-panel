import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';
import { previewStore, type PreviewState } from '$lib/stores/preview';
import { jobsStore } from '$lib/stores/jobs';
import type { GeneratePreviewEvent, GenerateJobStatus } from '$lib/api/types/jobs';

function stateWithJob(job: Partial<GenerateJobStatus>): PreviewState {
  return {
    loading: true,
    error: '',
    job: { job_id: 'job-1', status: 'running', n: 2, ...job },
    imageUrl: '',
    filename: '',
    prompt: 'test',
    streamingPreviews: {},
    compareSource: null
  };
}

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

describe('preview store streaming slots', () => {
  it('keeps unfinished units visible as other units complete and rejects late finals', () => {
    previewStore.setPreview(stateWithJob({ streaming: true }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, data_url: 'data:first' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, data_url: 'data:second' }));
    const running = { ...get(previewStore).job!, images: [{ image_id: 'first', image_url: '/api/image/first.png', filename: 'first.png', unit_index: 0 }], unit_statuses: { '0': 'success', '1': 'running' } };
    previewStore.setPreview(jobsStore.previewFromJob(running, get(previewStore)));
    expect(get(previewStore).streamingPreviews[0]).toBeUndefined();
    expect(get(previewStore).streamingPreviews[1].dataUrl).toBe('data:second');
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 99, data_url: 'data:late' }));
    expect(get(previewStore).streamingPreviews[0]).toBeUndefined();
    previewStore.setPreview(jobsStore.previewFromJob({ ...running, status: 'partial_failure', unit_statuses: { '0': 'success', '1': 'upstream_error' } }, get(previewStore)));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, sequence: 99 }));
    expect(get(previewStore).streamingPreviews).toEqual({});
  });

  it('keeps simultaneous Responses calls distinct and bounds incoming frames', () => {
    previewStore.setPreview(stateWithJob({ streaming: true }));
    previewStore.applyPreviewEvent(previewEvent({ call_index: 1, data_url: 'data:call-a' }));
    previewStore.applyPreviewEvent(previewEvent({ call_index: 2, data_url: 'data:call-b' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 11 }));
    previewStore.applyPreviewEvent(previewEvent({ call_index: 11 }));
    expect(Object.keys(get(previewStore).streamingPreviews)).toEqual(['0:1', '0:2']);
    previewStore.setError('cancelled');
    expect(get(previewStore).streamingPreviews).toEqual({});
  });
  it('buckets preview frames per unit index and drops stale sequences', () => {
    previewStore.setPreview(stateWithJob({}));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 1, data_url: 'data:unit0-1' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, sequence: 1, data_url: 'data:unit1-1' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 0, data_url: 'data:unit0-stale' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 2, data_url: 'data:unit0-2' }));

    const previews = get(previewStore).streamingPreviews;
    expect(previews[0]).toMatchObject({ dataUrl: 'data:unit0-2', sequence: 2 });
    expect(previews[1]).toMatchObject({ dataUrl: 'data:unit1-1', sequence: 1 });
    expect(Object.keys(previews)).toHaveLength(2);
  });

  it('ignores frames for other jobs and once the final image landed', () => {
    previewStore.setPreview(stateWithJob({}));
    previewStore.applyPreviewEvent(previewEvent({ job_id: 'other-job', data_url: 'data:other' }));
    expect(get(previewStore).streamingPreviews).toEqual({});

    previewStore.setPreview({ ...stateWithJob({ status: 'success' }), loading: false, imageUrl: '/api/image/final.png' });
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 5, data_url: 'data:late' }));
    expect(get(previewStore).streamingPreviews).toEqual({});
  });
});
