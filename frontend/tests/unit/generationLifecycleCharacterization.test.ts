import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { EventHandlers } from '$lib/api/events';
import type { GenerateJobResponse, GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';

const api = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  openJsonEventSource: vi.fn(),
  observeJobStatusChange: vi.fn()
}));

vi.mock('$lib/api/client', async (original) => ({
  ...(await original<typeof import('$lib/api/client')>()),
  apiFetch: api.apiFetch
}));
vi.mock('$lib/api/events', () => ({ openJsonEventSource: api.openJsonEventSource }));
vi.mock('$lib/utils/completionNotifications', () => ({ observeJobStatusChange: api.observeJobStatusChange }));

type Stream = {
  url: string;
  handlers: EventHandlers<GenerateJobStatus[] | GenerateJobStatus | GeneratePreviewEvent>;
  close: ReturnType<typeof vi.fn>;
  onopen: (() => void) | null;
};

class Page extends EventTarget {
  visibilityState = 'visible';
  show(visibility: string) {
    this.visibilityState = visibility;
    this.dispatchEvent(new Event('visibilitychange'));
  }
}

let page: Page;
let streams: Stream[];
let jobsStore: typeof import('$lib/stores/jobs')['jobsStore'];
let previewStore: typeof import('$lib/stores/preview')['previewStore'];
let initialPromptFormState: typeof import('$lib/utils/promptFormTypes')['initialPromptFormState'];

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function postResponse(jobId: string, overrides: Partial<GenerateJobResponse> = {}): GenerateJobResponse {
  return {
    job_id: jobId,
    status: 'queued',
    stage: 'queued',
    operation: 'generation',
    ...overrides
  };
}

function job(jobId: string, overrides: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return {
    job_id: jobId,
    status: 'running',
    n: 1,
    prompt: 'test prompt',
    updated_at: '2026-10-13T00:00:00',
    ...overrides
  };
}

function form(prompt: string, quantity = 1, extra: Partial<typeof initialPromptFormState> = {}) {
  return { ...initialPromptFormState, prompt, quantity, ...extra };
}

function emit(stream: Stream, event: string, data: GenerateJobStatus[] | GenerateJobStatus | GeneratePreviewEvent) {
  stream.handlers.onEvent({ event, data, lastEventId: 0 });
}

/**
 * Mirrors Workspace.svelte (lines 586-631 & 99) wiring between previewStore and
 * jobsStore so characterization tests pin both store-level contracts and the
 * Workspace lifecycle glue before extraction.
 */
function createWorkspaceLifecycleHarness() {
  const handledTerminalJobIds = new Set<string>();
  const galleryRefreshes: string[] = [];
  let galleryRefreshTimer: ReturnType<typeof setTimeout> | null = null;

  function scheduleGalleryRefreshAfterSuccess(jobId: string) {
    if (galleryRefreshTimer) clearTimeout(galleryRefreshTimer);
    galleryRefreshTimer = setTimeout(() => {
      galleryRefreshTimer = null;
      galleryRefreshes.push(jobId);
    }, 350);
  }

  function updatePreviewFromJob(snapshot: GenerateJobStatus) {
    previewStore.applyJob(snapshot);
    if (snapshot.status !== 'queued' && snapshot.status !== 'running') {
      if (handledTerminalJobIds.has(snapshot.job_id)) return;
      handledTerminalJobIds.add(snapshot.job_id);
      if (handledTerminalJobIds.size > 200) {
        const oldestJobId = handledTerminalJobIds.values().next().value;
        if (oldestJobId) handledTerminalJobIds.delete(oldestJobId);
      }
      if (jobsStore.shouldRefreshJobsAfterSubmit()) void jobsStore.loadJobs();
      jobsStore.markHistoryStale();
      if (snapshot.status === 'success' || snapshot.status === 'partial_failure') {
        scheduleGalleryRefreshAfterSuccess(snapshot.job_id);
      }
    }
  }

  function trackJob(jobId: string, meta?: { streaming?: boolean; status?: string }) {
    jobsStore.trackJob(
      jobId,
      (snapshot) => updatePreviewFromJob(snapshot),
      (message) => previewStore.failJobTracking(jobId, message),
      previewStore.applyPreviewEvent,
      meta
    );
  }

  const callbacks = {
    trackJob,
    releaseJobs: () => jobsStore.closeActiveJobSource(),
    releaseJob: (jobId: string) => jobsStore.untrackJob(jobId),
    get loadJobs() {
      return jobsStore.shouldRefreshJobsAfterSubmit() ? jobsStore.loadJobs : undefined;
    }
  };

  function clearAll() {
    if (galleryRefreshTimer) clearTimeout(galleryRefreshTimer);
    galleryRefreshTimer = null;
    jobsStore.closeActiveJobSource();
    previewStore.clearPreview();
  }

  return {
    callbacks,
    trackJob,
    updatePreviewFromJob,
    handledTerminalJobIds,
    galleryRefreshes,
    clearAll
  };
}

beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  page = new Page();
  vi.stubGlobal('document', page);
  streams = [];
  for (const mock of Object.values(api)) mock.mockReset();
  api.apiFetch.mockResolvedValue([]);
  api.openJsonEventSource.mockImplementation((url: string, handlers: Stream['handlers']) => {
    const stream: Stream = { url, handlers, close: vi.fn(), onopen: null };
    streams.push(stream);
    return stream as unknown as EventSource;
  });
  ({ jobsStore } = await import('$lib/stores/jobs'));
  ({ previewStore } = await import('$lib/stores/preview'));
  ({ initialPromptFormState } = await import('$lib/utils/promptFormTypes'));
});

afterEach(() => {
  jobsStore.cleanup();
  previewStore.cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('generation lifecycle characterization (previewStore + jobsStore)', () => {
  describe('terminal-once and snapshot deduplication', () => {
    it('delivers terminal update once at store level and deduplicates identical preview snapshots', async () => {
      jobsStore.startJobsEvents();
      streams[0].onopen?.();

      const onJobCalls: GenerateJobStatus[] = [];
      api.apiFetch.mockResolvedValueOnce(postResponse('job-1'));

      const ok = await previewStore.generateImage(form('streamed', 1, { stream: true }), {
        trackJob: (jobId, meta) => {
          jobsStore.trackJob(
            jobId,
            (snapshot) => {
              onJobCalls.push(snapshot);
              previewStore.applyJob(snapshot);
            },
            (message) => previewStore.failJobTracking(jobId, message),
            previewStore.applyPreviewEvent,
            meta
          );
        }
      });
      expect(ok).toBe(true);

      // Promote queued -> running via global feed to open per-job SSE stream.
      emit(streams[0], 'job', job('job-1', { status: 'running', streaming: true }));
      await vi.advanceTimersByTimeAsync(0);
      expect(streams).toHaveLength(2);
      expect(streams[1].url).toBe('/api/generate/job-1/events');

      const terminal = job('job-1', {
        status: 'success',
        streaming: true,
        images: [{ image_id: 'img-1', image_url: '/api/image/img-1.png', filename: 'img-1.png', unit_index: 0, result_index: 0 }]
      });
      emit(streams[1], 'job', terminal);
      await vi.advanceTimersByTimeAsync(0);

      // Tracker released on terminal; repeated terminal SSE/feed events do not re-invoke onJob.
      expect(streams[1].close).toHaveBeenCalledTimes(1);
      emit(streams[1], 'job', terminal);
      emit(streams[0], 'job', terminal);
      emit(streams[0], 'jobs', [terminal]);
      await vi.advanceTimersByTimeAsync(0);

      expect(onJobCalls).toHaveLength(2); // 1 running + 1 terminal
      const viewAfterTerminal = get(previewStore);
      expect(viewAfterTerminal.loading).toBe(false);
      expect(viewAfterTerminal.hasFinalImage).toBe(true);

      // Direct previewStore.applyJob with an identical terminal snapshot is a no-op (sameJobSnapshot).
      previewStore.applyJob({ ...terminal, images: [{ ...terminal.images![0] }] });
      expect(get(previewStore)).toBe(viewAfterTerminal);

      // Store layer alone does not mark history stale without Workspace's updatePreviewFromJob.
      expect(get(jobsStore).historyNeedsRefresh).toBe(false);
    });

    it('runs Workspace-style terminal side effects only once per job_id across repeated terminal callbacks', async () => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();
      // Load history first so stale marking is observable.
      api.apiFetch.mockResolvedValueOnce([]);
      await jobsStore.loadJobHistory();
      expect(get(jobsStore).historyNeedsRefresh).toBe(false);

      api.apiFetch.mockResolvedValueOnce(postResponse('job-dedup'));
      await previewStore.generateImage(form('dedup'), harness.callbacks);

      const terminal = job('job-dedup', {
        status: 'success',
        images: [{ image_id: 'd-1', image_url: '/api/image/d-1.png', filename: 'd-1.png', unit_index: 0 }]
      });
      emit(streams[0], 'job', terminal);
      await vi.advanceTimersByTimeAsync(350);

      expect(harness.handledTerminalJobIds.has('job-dedup')).toBe(true);
      expect(get(jobsStore).historyNeedsRefresh).toBe(true);
      expect(harness.galleryRefreshes).toEqual(['job-dedup']);

      // Simulate re-clearing stale flag and receiving a second terminal snapshot
      // (e.g. reopened or re-invoked updatePreviewFromJob for the same job_id).
      api.apiFetch.mockResolvedValueOnce([]);
      await jobsStore.refreshHistoryIfLoaded();
      expect(get(jobsStore).historyNeedsRefresh).toBe(false);

      harness.updatePreviewFromJob({ ...terminal, updated_at: '2026-10-13T00:00:05' });
      await vi.advanceTimersByTimeAsync(350);

      expect(get(jobsStore).historyNeedsRefresh).toBe(false);
      expect(harness.galleryRefreshes).toEqual(['job-dedup']);
    });
  });

  describe('loadJobs triggered only when feed is unhealthy', () => {
    it('calls loadJobs on submit and terminal when the global feed is unhealthy', async () => {
      const harness = createWorkspaceLifecycleHarness();
      // Feed started, not opened yet -> unhealthy.
      jobsStore.startJobsEvents();
      expect(jobsStore.shouldRefreshJobsAfterSubmit()).toBe(true);

      api.apiFetch.mockImplementation(async (url: string) => {
        if (url === '/api/generate') return postResponse('job-unhealthy');
        if (url === '/api/generate/jobs') return [job('job-unhealthy', { status: 'queued' })];
        return job('job-unhealthy', { status: 'running' });
      });

      const ok = await previewStore.generateImage(form('unhealthy'), harness.callbacks);
      expect(ok).toBe(true);

      const postAndSubmitLoads = api.apiFetch.mock.calls.map(([url]) => url);
      expect(postAndSubmitLoads).toEqual(['/api/generate', '/api/generate/jobs']);

      api.apiFetch.mockClear();
      api.apiFetch.mockResolvedValue([]);

      // Terminal arrives while feed is still unhealthy -> loadJobs fires again.
      harness.updatePreviewFromJob(job('job-unhealthy', { status: 'success' }));
      await vi.advanceTimersByTimeAsync(0);
      expect(api.apiFetch.mock.calls.map(([url]) => url)).toEqual(['/api/generate/jobs']);
    });

    it('skips loadJobs on submit and terminal when the global feed is healthy', async () => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();
      expect(jobsStore.shouldRefreshJobsAfterSubmit()).toBe(false);
      expect(harness.callbacks.loadJobs).toBeUndefined();

      api.apiFetch.mockResolvedValueOnce(postResponse('job-healthy'));
      const ok = await previewStore.generateImage(form('healthy'), harness.callbacks);
      expect(ok).toBe(true);
      expect(api.apiFetch.mock.calls.map(([url]) => url)).toEqual(['/api/generate']);

      api.apiFetch.mockClear();
      emit(streams[0], 'job', job('job-healthy', {
        status: 'success',
        images: [{ image_id: 'h-1', image_url: '/api/image/h-1.png', filename: 'h-1.png', unit_index: 0 }]
      }));
      await vi.advanceTimersByTimeAsync(0);

      expect(api.apiFetch).not.toHaveBeenCalled();
    });

    it('triggers loadJobs on terminal if the feed drops mid-flight', async () => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();
      expect(jobsStore.shouldRefreshJobsAfterSubmit()).toBe(false);

      api.apiFetch.mockResolvedValueOnce(postResponse('job-drop'));
      await previewStore.generateImage(form('mid-drop', 1, { stream: true }), harness.callbacks);
      expect(api.apiFetch.mock.calls.map(([url]) => url)).toEqual(['/api/generate']);

      // Global feed drops -> shouldRefreshJobsAfterSubmit flips to true.
      api.apiFetch.mockClear();
      api.apiFetch.mockResolvedValue([]);
      streams[0].handlers.onNetworkError?.(new Event('error'));
      await vi.advanceTimersByTimeAsync(0);
      expect(jobsStore.shouldRefreshJobsAfterSubmit()).toBe(true);

      api.apiFetch.mockClear();
      harness.updatePreviewFromJob(job('job-drop', { status: 'success' }));
      await vi.advanceTimersByTimeAsync(0);
      expect(api.apiFetch.mock.calls.map(([url]) => url)).toEqual(['/api/generate/jobs']);
    });
  });

  describe('history stale marking on terminal', () => {
    it('keeps historyNeedsRefresh false while active and marks it true upon terminal', async () => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();

      api.apiFetch.mockResolvedValueOnce([job('old-1', { status: 'success' })]);
      await jobsStore.loadJobHistory();
      expect(get(jobsStore)).toMatchObject({ historyLoaded: true, historyNeedsRefresh: false });

      api.apiFetch.mockResolvedValueOnce(postResponse('job-hist'));
      await previewStore.generateImage(form('history test'), harness.callbacks);

      emit(streams[0], 'job', job('job-hist', { status: 'running' }));
      await vi.advanceTimersByTimeAsync(0);
      expect(get(jobsStore).historyNeedsRefresh).toBe(false);

      emit(streams[0], 'job', job('job-hist', { status: 'error', error: 'boom' }));
      await vi.advanceTimersByTimeAsync(0);
      expect(get(jobsStore).historyNeedsRefresh).toBe(true);

      api.apiFetch.mockResolvedValueOnce([
        job('job-hist', { status: 'error', error: 'boom' }),
        job('old-1', { status: 'success' })
      ]);
      await jobsStore.refreshHistoryIfLoaded();
      expect(get(jobsStore).historyNeedsRefresh).toBe(false);
      expect(get(jobsStore).historyJobs.map((entry) => entry.job_id)).toEqual(['job-hist', 'old-1']);
    });
  });

  describe('gallery refresh behavior by terminal status', () => {
    it.each(['success', 'partial_failure'] as const)('schedules gallery refresh on %s', async (terminalStatus) => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();

      api.apiFetch.mockResolvedValueOnce(postResponse(`job-${terminalStatus}`));
      await previewStore.generateImage(form('gallery check', 2), harness.callbacks);

      emit(streams[0], 'job', job(`job-${terminalStatus}`, {
        status: terminalStatus,
        n: 2,
        images: [{ image_id: 'g-1', image_url: '/api/image/g-1.png', filename: 'g-1.png', unit_index: 0 }]
      }));
      await vi.advanceTimersByTimeAsync(0);
      // Debounced 350ms in Workspace.
      expect(harness.galleryRefreshes).toEqual([]);

      await vi.advanceTimersByTimeAsync(350);
      expect(harness.galleryRefreshes).toEqual([`job-${terminalStatus}`]);
    });

    it.each(['error', 'upstream_error', 'cancelled'] as const)('does not refresh gallery on %s while still marking history stale', async (terminalStatus) => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();

      api.apiFetch.mockResolvedValueOnce(postResponse(`job-${terminalStatus}`));
      await previewStore.generateImage(form('no gallery refresh'), harness.callbacks);

      emit(streams[0], 'job', job(`job-${terminalStatus}`, {
        status: terminalStatus,
        error: terminalStatus === 'cancelled' ? null : 'failed'
      }));
      await vi.advanceTimersByTimeAsync(500);

      expect(harness.galleryRefreshes).toEqual([]);
      expect(get(jobsStore).historyNeedsRefresh).toBe(true);
      const view = get(previewStore);
      expect(view.loading).toBe(false);
      expect(view.slots[0].status).toBe(terminalStatus === 'cancelled' ? 'cancelled' : 'failed');
    });
  });

  describe('reverse-order POST responses across previewStore and jobsStore', () => {
    it('keeps click slot order, tracks both jobs concurrently, and preserves first completed selection', async () => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();

      const firstPost = deferred<GenerateJobResponse>();
      const secondPost = deferred<GenerateJobResponse>();
      api.apiFetch
        .mockReturnValueOnce(firstPost.promise)
        .mockReturnValueOnce(secondPost.promise);

      const submitFirst = previewStore.generateImage(form('first prompt', 2, { stream: true }), harness.callbacks);
      const submitSecond = previewStore.generateImage(form('second prompt', 1, { stream: true }), harness.callbacks);

      // Slots reserved synchronously before either POST resolves.
      expect(get(previewStore).slots.map((slot) => [slot.prompt, slot.unitIndex, slot.status])).toEqual([
        ['first prompt', 0, 'waiting'],
        ['first prompt', 1, 'waiting'],
        ['second prompt', 0, 'waiting']
      ]);

      // Second POST resolves first.
      secondPost.resolve(postResponse('job-second'));
      expect(await submitSecond).toBe(true);

      // First POST resolves second.
      firstPost.resolve(postResponse('job-first'));
      expect(await submitFirst).toBe(true);

      // Slot order stays in click order, not POST response order.
      expect(get(previewStore).slots.map((slot) => slot.jobId)).toEqual(['job-first', 'job-first', 'job-second']);

      // Promote both queued streaming jobs to running -> each gets its own per-job SSE stream.
      emit(streams[0], 'job', job('job-second', { status: 'running', streaming: true }));
      emit(streams[0], 'job', job('job-first', { status: 'running', n: 2, streaming: true }));
      await vi.advanceTimersByTimeAsync(0);
      expect(streams.map((stream) => stream.url)).toEqual([
        '/api/generate/jobs/events',
        '/api/generate/job-second/events',
        '/api/generate/job-first/events'
      ]);

      // job-second finishes first; only its stream closes and its slot becomes the selected image.
      emit(streams[1], 'job', job('job-second', {
        status: 'success',
        prompt: 'second prompt',
        images: [{ image_id: 'img-2', image_url: '/api/image/img-2.png', filename: 'img-2.png', unit_index: 0, result_index: 0 }]
      }));
      await vi.advanceTimersByTimeAsync(0);

      expect(streams[1].close).toHaveBeenCalledTimes(1);
      expect(streams[2].close).not.toHaveBeenCalled();
      expect(get(previewStore).selectedSlot?.jobId).toBe('job-second');
      expect(get(previewStore).loading).toBe(true);

      // job-first completes later; its images land in slots 0..1 without stealing selection.
      emit(streams[2], 'job', job('job-first', {
        status: 'success',
        n: 2,
        prompt: 'first prompt',
        images: [
          { image_id: 'img-1a', image_url: '/api/image/img-1a.png', filename: 'img-1a.png', unit_index: 0, result_index: 0 },
          { image_id: 'img-1b', image_url: '/api/image/img-1b.png', filename: 'img-1b.png', unit_index: 1, result_index: 0 }
        ]
      }));
      await vi.advanceTimersByTimeAsync(350);

      const finalView = get(previewStore);
      expect(streams[2].close).toHaveBeenCalledTimes(1);
      expect(finalView.loading).toBe(false);
      expect(finalView.completedSlots.map((slot) => slot.image?.image_id)).toEqual(['img-1a', 'img-1b', 'img-2']);
      expect(finalView.selectedSlot?.image?.image_id).toBe('img-2');
      // Both finished within 350ms, so the debounced gallery refresh coalesces.
      expect(harness.galleryRefreshes).toEqual(['job-first']);
    });
  });

  describe('late response after clear', () => {
    it('ignores late POST responses and late tracked-job SSE/polls after clearPreview + closeActiveJobSource', async () => {
      const harness = createWorkspaceLifecycleHarness();
      jobsStore.startJobsEvents();
      streams[0].onopen?.();

      // One already-accepted streaming job + one in-flight POST.
      api.apiFetch.mockResolvedValueOnce(postResponse('job-active', { status: 'running' }));
      await previewStore.generateImage(form('active', 1, { stream: true }), harness.callbacks);
      expect(streams).toHaveLength(2);

      const slowPost = deferred<GenerateJobResponse>();
      api.apiFetch.mockReturnValueOnce(slowPost.promise);
      const pendingSubmit = previewStore.generateImage(form('slow'), harness.callbacks);

      // Clear preview and release all tracked jobs before slowPost resolves.
      harness.clearAll();
      expect(streams[1].close).toHaveBeenCalledTimes(1);
      expect(get(previewStore).slots).toEqual([]);
      expect(get(previewStore).loading).toBe(false);

      // Late POST resolves and late SSE arrives for the cleared job.
      slowPost.resolve(postResponse('job-late'));
      expect(await pendingSubmit).toBe(true);
      emit(streams[1], 'job', job('job-active', {
        status: 'success',
        images: [{ image_id: 'a-1', image_url: '/api/image/a-1.png', filename: 'a-1.png', unit_index: 0 }]
      }));
      emit(streams[0], 'job', job('job-active', { status: 'success' }));
      await vi.advanceTimersByTimeAsync(500);

      // Neither job-late nor job-active mutates the cleared group or opens a new stream.
      expect(streams).toHaveLength(2);
      expect(get(previewStore).slots).toEqual([]);
      expect(get(previewStore).loading).toBe(false);
      expect(get(previewStore).hasFinalImage).toBe(false);
      expect(get(jobsStore).historyNeedsRefresh).toBe(false);
      expect(harness.galleryRefreshes).toEqual([]);

      // A new submission after clear works independently without interference from job-late.
      api.apiFetch.mockResolvedValueOnce(postResponse('job-fresh'));
      expect(await previewStore.generateImage(form('fresh'), harness.callbacks)).toBe(true);
      expect(get(previewStore).slots.map((slot) => slot.jobId)).toEqual(['job-fresh']);
    });
  });
});
