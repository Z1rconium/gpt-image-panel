import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';
import { previewStore, type PreviewState } from '$lib/stores/preview';
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
  it('buckets preview frames per unit index and drops stale sequences', () => {
    previewStore.setPreview(stateWithJob({}));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 1, data_url: 'data:unit0-1' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 1, sequence: 1, data_url: 'data:unit1-1' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 0, data_url: 'data:unit0-stale' }));
    previewStore.applyPreviewEvent(previewEvent({ unit_index: 0, sequence: 2, data_url: 'data:unit0-2' }));

    const previews = get(previewStore).streamingPreviews;
    expect(previews[0]).toEqual({ dataUrl: 'data:unit0-2', sequence: 2 });
    expect(previews[1]).toEqual({ dataUrl: 'data:unit1-1', sequence: 1 });
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
