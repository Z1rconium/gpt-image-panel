import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';
import type { EventHandlers } from '$lib/api/events';

const api = vi.hoisted(() => ({ apiFetch: vi.fn(), openJsonEventSource: vi.fn(), observeJobStatusChange: vi.fn() }));
vi.mock('$lib/api/client', async (original) => ({ ...await original<typeof import('$lib/api/client')>(), apiFetch: api.apiFetch }));
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
function job(id: string, extra: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return { job_id: id, status: 'running', updated_at: '2026-10-13T00:00:00', ...extra };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function emit(stream: Stream, event: string, data: GenerateJobStatus[] | GenerateJobStatus | GeneratePreviewEvent) {
  stream.handlers.onEvent({ event, data, lastEventId: 0 });
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
});
afterEach(() => {
  jobsStore.cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('global jobs feed lifecycle', () => {
  it('ignores a list response after cleanup, including notification observations', async () => {
    const slow = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(slow.promise);
    const loading = jobsStore.loadJobs();
    jobsStore.cleanup();
    slow.resolve([job('late')]);
    await loading;
    expect(get(jobsStore).jobs).toEqual([]);
    expect(api.observeJobStatusChange).not.toHaveBeenCalled();
  });

  it('ignores a list response from the lifecycle before a replacement source', async () => {
    const slow = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(slow.promise);
    const loading = jobsStore.loadJobs();
    jobsStore.startJobsEvents();
    slow.resolve([job('old-list')]);
    await loading;
    expect(get(jobsStore).jobs).toEqual([]);
  });

  it('keeps the newer of two list requests regardless of their completion order', async () => {
    const slow = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(slow.promise).mockResolvedValueOnce([job('new')]);
    const older = jobsStore.loadJobs();
    await jobsStore.loadJobs();
    slow.resolve([job('old')]);
    await older;
    expect(get(jobsStore).jobs.map((value) => value.job_id)).toEqual(['new']);
  });

  it('does not let an HTTP snapshot replace an SSE snapshot accepted while it was loading', async () => {
    jobsStore.startJobsEvents();
    const slow = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(slow.promise);
    const loading = jobsStore.loadJobs();
    emit(streams[0], 'jobs', [job('live')]);
    slow.resolve([job('old-list')]);
    await loading;
    expect(get(jobsStore).jobs.map((value) => value.job_id)).toEqual(['live']);
  });

  it('does not replay a late list status after a current single-job terminal event', async () => {
    const update = vi.fn().mockResolvedValue(undefined);
    jobsStore.startJobsEvents();
    jobsStore.trackJob('tracked', update, vi.fn());
    const slow = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(slow.promise);
    const loading = jobsStore.loadJobs();
    const completed = job('tracked', { status: 'success' });
    emit(streams[0], 'job', completed);
    await vi.advanceTimersByTimeAsync(0);
    slow.resolve([job('tracked')]);
    await loading;
    expect(update).toHaveBeenCalledExactlyOnceWith(completed);
    expect(api.observeJobStatusChange).not.toHaveBeenCalledWith(job('tracked'));
    expect(streams[1].close).toHaveBeenCalledTimes(1);
    expect(get(jobsStore).jobs).toEqual([]);
  });

  it('drops every callback from a replaced source without marking it healthy or polling', async () => {
    jobsStore.startJobsEvents();
    const stale = streams[0];
    jobsStore.startJobsEvents();
    stale.onopen?.();
    expect(jobsStore.shouldRefreshJobsAfterSubmit()).toBe(true);
    emit(stale, 'jobs', [job('stale')]);
    emit(stale, 'job', job('stale-completion', { status: 'success' }));
    expect(get(jobsStore).jobs).toEqual([]);
    expect(api.observeJobStatusChange).not.toHaveBeenCalled();
    stale.handlers.onNetworkError?.(new Event('error'));
    stale.handlers.onError?.();
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.apiFetch).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    emit(streams[1], 'jobs', [job('current')]);
    expect(get(jobsStore).jobs[0].job_id).toBe('current');
    expect(jobsStore.shouldRefreshJobsAfterSubmit()).toBe(false);
  });

  it('drops every callback after cleanup without restarting resources', async () => {
    jobsStore.startJobsEvents();
    const stale = streams[0];
    jobsStore.cleanup();
    jobsStore.cleanup();
    stale.onopen?.();
    emit(stale, 'jobs', [job('stale')]);
    emit(stale, 'job', job('stale', { status: 'success' }));
    stale.handlers.onNetworkError?.(new Event('error'));
    stale.handlers.onError?.();
    page.show('visible');
    await vi.advanceTimersByTimeAsync(10000);
    expect(stale.close).toHaveBeenCalledTimes(1);
    expect(get(jobsStore).jobs).toEqual([]);
    expect(api.observeJobStatusChange).not.toHaveBeenCalled();
    expect(api.apiFetch).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('restores one visibility listener after cleanup and load/start reuse', async () => {
    jobsStore.startJobsEvents();
    jobsStore.cleanup();
    await jobsStore.loadJobs();
    jobsStore.startJobsEvents();
    jobsStore.startJobsEvents();
    api.apiFetch.mockClear();
    page.show('hidden');
    page.show('visible');
    await vi.advanceTimersByTimeAsync(0);
    expect(api.apiFetch).toHaveBeenCalledTimes(1);
  });

  it('polls only while visible and unhealthy, then stops on current-source open/events', async () => {
    jobsStore.startJobsEvents();
    page.show('hidden');
    streams[0].handlers.onNetworkError?.(new Event('error'));
    streams[0].handlers.onError?.();
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.apiFetch).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(1);
    page.show('visible');
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.apiFetch).toHaveBeenCalledTimes(2);
    streams[0].onopen?.();
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.apiFetch).toHaveBeenCalledTimes(2);
    streams[0].handlers.onNetworkError?.(new Event('error'));
    await vi.advanceTimersByTimeAsync(0);
    emit(streams[0], 'jobs', [job('live')]);
    api.apiFetch.mockClear();
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.apiFetch).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps an old poll finally from unlocking a new lifecycle poll', async () => {
    const old = deferred<GenerateJobStatus[]>();
    const current = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    jobsStore.startJobsEvents();
    streams[0].handlers.onNetworkError?.(new Event('error'));
    jobsStore.cleanup();
    jobsStore.startJobsEvents();
    streams[1].handlers.onNetworkError?.(new Event('error'));
    expect(api.apiFetch).toHaveBeenCalledTimes(2);
    old.resolve([job('old')]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.apiFetch).toHaveBeenCalledTimes(2);
    current.resolve([job('current')]);
    await vi.advanceTimersByTimeAsync(0);
    expect(get(jobsStore).jobs[0].job_id).toBe('current');
  });

  it('preserves list equality, selection pruning, and observing each accepted status', async () => {
    api.apiFetch.mockResolvedValue([job('a'), job('b')]);
    await jobsStore.loadJobs();
    jobsStore.toggleAll();
    const previous = get(jobsStore);
    await jobsStore.loadJobs();
    expect(get(jobsStore)).toBe(previous);
    expect(api.observeJobStatusChange).toHaveBeenCalledTimes(4);
    api.apiFetch.mockResolvedValue([job('b')]);
    await jobsStore.loadJobs();
    expect([...get(jobsStore).selectedIds]).toEqual(['b']);
  });

  it('keeps tracked preview events and terminal updates alongside global notifications', async () => {
    const update = vi.fn().mockResolvedValue(undefined);
    const preview = vi.fn();
    jobsStore.startJobsEvents();
    jobsStore.trackJob('tracked', update, vi.fn(), preview);
    const partial: GeneratePreviewEvent = { job_id: 'tracked', unit_index: 0, partial_image_index: 0, sequence: 1, mime_type: 'image/png', data_url: 'data:partial' };
    emit(streams[1], 'preview', partial);
    expect(preview).toHaveBeenCalledWith(partial);
    emit(streams[0], 'job', job('other', { status: 'success' }));
    expect(update).not.toHaveBeenCalled();
    expect(api.observeJobStatusChange).toHaveBeenCalledWith(job('other', { status: 'success' }));
    emit(streams[0], 'jobs', [job('tracked')]);
    expect(update).toHaveBeenCalledWith(job('tracked'));
    emit(streams[1], 'job', job('tracked', { status: 'success' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(streams[1].close).toHaveBeenCalledTimes(1);
    emit(streams[1], 'preview', partial);
    expect(preview).toHaveBeenCalledTimes(1);
  });

  it('restores tracked fallback polling on visibility after cleanup and reuse', async () => {
    jobsStore.cleanup();
    page.show('hidden');
    const update = vi.fn().mockResolvedValue(undefined);
    jobsStore.trackJob('tracked', update, vi.fn());
    streams[0].handlers.onNetworkError?.(new Event('error'));
    expect(api.apiFetch).not.toHaveBeenCalled();
    api.apiFetch.mockImplementation((url: string) => Promise.resolve(url === '/api/generate/jobs' ? [] : job('tracked')));
    page.show('visible');
    await vi.advanceTimersByTimeAsync(0);
    expect(update).toHaveBeenCalledWith(job('tracked'));
    expect(api.apiFetch.mock.calls.map(([url]) => url)).toEqual(['/api/generate/jobs', '/api/generate/tracked']);
  });

  it('keeps paginated history deduplication, stale refresh and cleanup guards', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => job(String(index)));
    api.apiFetch.mockResolvedValueOnce(firstPage).mockResolvedValueOnce([job('49'), job('50')]);
    await jobsStore.loadJobHistory();
    await jobsStore.loadMoreJobHistory();
    expect(get(jobsStore).historyJobs).toHaveLength(51);
    expect(api.apiFetch.mock.calls[1][0]).toContain('before_job_id=49');
    jobsStore.markHistoryStale();
    api.apiFetch.mockResolvedValueOnce([job('failed', { status: 'upstream_error' })]);
    await jobsStore.setHistoryFailedOnly(true);
    expect(api.apiFetch.mock.calls[2][0]).toContain('failed_only=true');
    expect(get(jobsStore)).toMatchObject({ historyFailedOnly: true, historyNeedsRefresh: false });
    const pending = deferred<GenerateJobStatus[]>();
    api.apiFetch.mockReturnValueOnce(pending.promise);
    const history = jobsStore.loadJobHistory();
    jobsStore.cleanup();
    pending.resolve([job('stale-history')]);
    await history;
    expect(get(jobsStore).historyJobs.map((value) => value.job_id)).toEqual(['failed']);
  });
});
