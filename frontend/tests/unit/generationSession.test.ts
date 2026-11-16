import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { GenerateJobResponse, GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';
import {
  createGenerationSession,
  type GenerationSessionJobs,
  type GenerationTransport
} from '$lib/features/workspace/generationSession';
import { editSourceStore } from '$lib/stores/editSource';
import { previewStore } from '$lib/stores/preview';
import { initialPromptFormState } from '$lib/utils/promptFormTypes';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function makeJob(jobId: string, overrides: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return {
    job_id: jobId,
    status: 'running',
    n: 1,
    prompt: 'test prompt',
    ...overrides
  };
}

function makeForm(prompt = 'sunset over mountains', quantity = 1) {
  return { ...initialPromptFormState, prompt, quantity };
}

type TrackedEntry = {
  jobId: string;
  onJob: (job: GenerateJobStatus) => void | Promise<void>;
  onError: (message: string) => void;
  onPreview?: (event: GeneratePreviewEvent) => void;
  meta?: { streaming?: boolean; status?: string };
};

function createFakeJobsHarness(options: { feedHealthy?: boolean } = {}) {
  let feedHealthy = options.feedHealthy ?? true;
  const tracked = new Map<string, TrackedEntry>();
  const loadJobs = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
  const markHistoryStale = vi.fn();
  const untrackJob = vi.fn((jobId: string) => {
    tracked.delete(jobId);
  });
  const closeActiveJobSource = vi.fn(() => {
    tracked.clear();
  });

  const jobs: GenerationSessionJobs = {
    trackJob: vi.fn((jobId, onJob, onError, onPreview, meta) => {
      tracked.set(jobId, { jobId, onJob, onError, onPreview, meta });
    }),
    untrackJob,
    closeActiveJobSource,
    loadJobs,
    markHistoryStale,
    shouldRefreshJobsAfterSubmit: () => !feedHealthy
  };

  return {
    jobs,
    tracked,
    loadJobs,
    markHistoryStale,
    untrackJob,
    closeActiveJobSource,
    setFeedHealthy(healthy: boolean) {
      feedHealthy = healthy;
    },
    emitJob(snapshot: GenerateJobStatus) {
      return tracked.get(snapshot.job_id)?.onJob(snapshot);
    },
    emitPreview(event: GeneratePreviewEvent) {
      tracked.get(event.job_id)?.onPreview?.(event);
    },
    emitError(jobId: string, message: string) {
      tracked.get(jobId)?.onError(message);
    }
  };
}

describe('generationSession', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    previewStore.clearPreview();
    editSourceStore.reset();
  });

  afterEach(() => {
    previewStore.cleanup();
    vi.useRealTimers();
  });

  it('runs submit -> track -> stream preview -> apply -> terminal lifecycle with scripted transport', async () => {
    const jobsHarness = createFakeJobsHarness({ feedHealthy: true });
    const postGenerate = vi.fn<GenerationTransport['postGenerate']>().mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      stage: 'queued',
      operation: 'generation'
    });
    const postEdit = vi.fn<GenerationTransport['postEdit']>();
    const getJob = vi.fn<GenerationTransport['getJob']>();
    const onGalleryRefresh = vi.fn();
    const onShowNewGalleryToast = vi.fn();

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh,
      onShowNewGalleryToast,
      isGalleryFirstPage: () => true,
      transport: { postGenerate, postEdit, getJob }
    });

    const ok = await session.submitGenerate(makeForm('streamed cat', 1) && { ...makeForm('streamed cat', 1), stream: true });
    expect(ok).toBe(true);
    expect(postGenerate).toHaveBeenCalledTimes(1);
    expect(jobsHarness.loadJobs).not.toHaveBeenCalled();
    expect(jobsHarness.tracked.get('job-1')?.meta).toEqual({ streaming: true, status: 'queued' });

    // Streamed preview frame updates the pending slot.
    jobsHarness.emitPreview({
      job_id: 'job-1',
      unit_index: 0,
      partial_image_index: 0,
      sequence: 1,
      mime_type: 'image/png',
      data_url: 'data:image/png;base64,partial'
    });
    expect(get(previewStore).slots[0].status).toBe('streaming');
    expect(get(previewStore).slots[0].previewUrl).toBe('data:image/png;base64,partial');

    // Running update keeps history clean and does not refresh gallery.
    await jobsHarness.emitJob(makeJob('job-1', { status: 'running', streaming: true }));
    expect(jobsHarness.markHistoryStale).not.toHaveBeenCalled();
    expect(onGalleryRefresh).not.toHaveBeenCalled();

    // Terminal success triggers history stale + debounced gallery refresh once.
    const terminal = makeJob('job-1', {
      status: 'success',
      images: [{ image_id: 'img-1', image_url: '/api/image/img-1.png', filename: 'img-1.png', unit_index: 0, result_index: 0 }]
    });
    await jobsHarness.emitJob(terminal);
    await jobsHarness.emitJob({ ...terminal, updated_at: '2026-11-16T00:00:01Z' });

    expect(get(previewStore).hasFinalImage).toBe(true);
    expect(get(previewStore).loading).toBe(false);
    expect(jobsHarness.markHistoryStale).toHaveBeenCalledTimes(1);
    expect(onGalleryRefresh).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(350);
    expect(onGalleryRefresh).toHaveBeenCalledTimes(1);
    expect(onShowNewGalleryToast).not.toHaveBeenCalled();

    session.dispose();
  });

  it('shows new-gallery toast instead of refreshing when not on gallery page 1', async () => {
    const jobsHarness = createFakeJobsHarness({ feedHealthy: false });
    const postGenerate = vi.fn<GenerationTransport['postGenerate']>().mockResolvedValue({
      job_id: 'job-p2',
      status: 'queued',
      operation: 'generation'
    });
    const onGalleryRefresh = vi.fn();
    const onShowNewGalleryToast = vi.fn();

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh,
      onShowNewGalleryToast,
      isGalleryFirstPage: () => false,
      transport: { postGenerate }
    });

    await session.submitGenerate(makeForm('page two'));
    // Unhealthy feed refreshes list on submit and on terminal.
    expect(jobsHarness.loadJobs).toHaveBeenCalledTimes(1);

    await jobsHarness.emitJob(makeJob('job-p2', { status: 'partial_failure' }));
    expect(jobsHarness.loadJobs).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(350);
    expect(onGalleryRefresh).not.toHaveBeenCalled();
    expect(onShowNewGalleryToast).toHaveBeenCalledTimes(1);

    session.dispose();
  });

  it('does not schedule gallery refresh on failed or cancelled jobs while marking history stale', async () => {
    const jobsHarness = createFakeJobsHarness();
    const postGenerate = vi.fn<GenerationTransport['postGenerate']>()
      .mockResolvedValueOnce({ job_id: 'job-err', status: 'queued', operation: 'generation' })
      .mockResolvedValueOnce({ job_id: 'job-cancel', status: 'queued', operation: 'generation' });
    const onGalleryRefresh = vi.fn();

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh,
      onShowNewGalleryToast: vi.fn(),
      isGalleryFirstPage: () => true,
      transport: { postGenerate }
    });

    await session.submitGenerate(makeForm('will fail'));
    await jobsHarness.emitJob(makeJob('job-err', { status: 'error', error: 'boom' }));
    await session.submitGenerate(makeForm('will cancel'));
    await jobsHarness.emitJob(makeJob('job-cancel', { status: 'cancelled' }));

    await vi.advanceTimersByTimeAsync(500);
    expect(onGalleryRefresh).not.toHaveBeenCalled();
    expect(jobsHarness.markHistoryStale).toHaveBeenCalledTimes(2);

    session.dispose();
  });

  it('caps handledTerminalJobIds at 200 entries with FIFO eviction', async () => {
    const jobsHarness = createFakeJobsHarness();
    let seq = 0;
    const postGenerate = vi.fn<GenerationTransport['postGenerate']>().mockImplementation(async () => ({
      job_id: `job-${seq++}`,
      status: 'queued',
      operation: 'generation'
    }));

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh: vi.fn(),
      onShowNewGalleryToast: vi.fn(),
      isGalleryFirstPage: () => true,
      transport: { postGenerate }
    });

    // Track and complete 201 distinct jobs directly via resumeJob + emitJob.
    for (let i = 0; i <= 200; i += 1) {
      const id = `evict-${i}`;
      session.resumeJob(makeJob(id, { status: 'running' }));
      await jobsHarness.emitJob(makeJob(id, { status: 'cancelled' }));
    }
    expect(jobsHarness.markHistoryStale).toHaveBeenCalledTimes(201);

    // evict-1 is still remembered -> ignored; evict-0 was evicted -> processed again.
    jobsHarness.markHistoryStale.mockClear();
    session.resumeJob(makeJob('evict-1', { status: 'running' }));
    await jobsHarness.emitJob(makeJob('evict-1', { status: 'cancelled' }));
    expect(jobsHarness.markHistoryStale).not.toHaveBeenCalled();

    session.resumeJob(makeJob('evict-0', { status: 'running' }));
    await jobsHarness.emitJob(makeJob('evict-0', { status: 'cancelled' }));
    expect(jobsHarness.markHistoryStale).toHaveBeenCalledTimes(1);

    session.dispose();
  });

  it('submits edits through transport.postEdit and releases replaced generation trackers', async () => {
    const jobsHarness = createFakeJobsHarness();
    const postGenerate = vi.fn<GenerationTransport['postGenerate']>().mockResolvedValue({
      job_id: 'job-gen',
      status: 'queued',
      operation: 'generation'
    });
    const postEdit = vi.fn<GenerationTransport['postEdit']>().mockResolvedValue({
      job_id: 'job-edit',
      status: 'queued',
      operation: 'edit'
    });

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh: vi.fn(),
      onShowNewGalleryToast: vi.fn(),
      isGalleryFirstPage: () => true,
      transport: { postGenerate, postEdit }
    });

    await session.submitGenerate(makeForm('gen first'));
    editSourceStore.addFiles([new File([new Uint8Array([1])], 'source.png', { type: 'image/png' })], () => {});
    const edited = await session.submitEdit(makeForm('edit it'), get(editSourceStore));

    expect(edited).toBe(true);
    expect(postEdit).toHaveBeenCalledTimes(1);
    expect(postEdit.mock.calls[0][0]).toBe('/api/edits');
    expect(jobsHarness.untrackJob).toHaveBeenCalledWith('job-gen');
    expect(jobsHarness.tracked.has('job-edit')).toBe(true);

    jobsHarness.emitError('job-edit', 'lost connection');
    expect(get(previewStore).submissionErrors[0]?.message).toBe('lost connection');

    session.dispose();
  });

  it('opens a job via transport.getJob, tracks active jobs, and drops superseded fetches', async () => {
    const jobsHarness = createFakeJobsHarness();
    const firstFetch = deferred<GenerateJobStatus>();
    const secondFetch = deferred<GenerateJobStatus>();
    const getJob = vi.fn<GenerationTransport['getJob']>()
      .mockReturnValueOnce(firstFetch.promise)
      .mockReturnValueOnce(secondFetch.promise)
      .mockResolvedValueOnce(makeJob('job-done', {
        status: 'success',
        images: [{ image_id: 'd1', image_url: '/api/image/d1.png', filename: 'd1.png', unit_index: 0 }]
      }));
    const onBeforeOpen = vi.fn();

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh: vi.fn(),
      onShowNewGalleryToast: vi.fn(),
      isGalleryFirstPage: () => true,
      transport: { getJob }
    });

    const p1 = session.openJob('job-stale', onBeforeOpen);
    const p2 = session.openJob('job-active', onBeforeOpen);

    secondFetch.resolve(makeJob('job-active', { status: 'running', streaming: true }));
    expect((await p2)?.job_id).toBe('job-active');
    expect(jobsHarness.closeActiveJobSource).toHaveBeenCalledTimes(1);
    expect(onBeforeOpen).toHaveBeenCalledTimes(1);
    expect(jobsHarness.tracked.has('job-active')).toBe(true);

    // Late resolution of superseded first openJob is ignored.
    firstFetch.resolve(makeJob('job-stale', { status: 'running' }));
    expect(await p1).toBeNull();
    expect(get(previewStore).job?.job_id).toBe('job-active');

    // Opening a terminal job does not register a tracker.
    const done = await session.openJob('job-done');
    expect(done?.job_id).toBe('job-done');
    expect(jobsHarness.tracked.has('job-done')).toBe(false);
    expect(get(previewStore).hasFinalImage).toBe(true);

    session.dispose();
  });

  it('clears preview and cancels pending timers/callbacks on dispose()', async () => {
    const jobsHarness = createFakeJobsHarness();
    const delayedGet = deferred<GenerateJobStatus>();
    const postGenerate = vi.fn<GenerationTransport['postGenerate']>().mockResolvedValue({
      job_id: 'job-dispose',
      status: 'queued',
      operation: 'generation'
    });
    const getJob = vi.fn<GenerationTransport['getJob']>().mockReturnValueOnce(delayedGet.promise);
    const onGalleryRefresh = vi.fn();

    const session = createGenerationSession({
      preview: previewStore,
      jobs: jobsHarness.jobs,
      onGalleryRefresh,
      onShowNewGalleryToast: vi.fn(),
      isGalleryFirstPage: () => true,
      transport: { postGenerate, getJob }
    });

    await session.submitGenerate(makeForm('before dispose'));
    await jobsHarness.emitJob(makeJob('job-dispose', { status: 'success' }));

    const pendingOpen = session.openJob('job-late');
    session.clearPreview();
    expect(get(previewStore).slots).toEqual([]);
    expect(jobsHarness.closeActiveJobSource).toHaveBeenCalledTimes(1);

    session.dispose();
    await vi.advanceTimersByTimeAsync(500);
    expect(onGalleryRefresh).not.toHaveBeenCalled();

    delayedGet.resolve(makeJob('job-late', { status: 'running' }));
    expect(await pendingOpen).toBeNull();
    expect(await session.submitGenerate(makeForm('after dispose'))).toBe(false);
    expect(postGenerate).toHaveBeenCalledTimes(1);
  });
});
