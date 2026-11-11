import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { observeJobStatusChange } from '$lib/utils/completionNotifications';
import type { GeneratePreviewEvent, GenerateJobStatus } from '$lib/api/types/jobs';
import { createJobsFeedController } from './jobsFeed';
import { HISTORY_PAGE_SIZE, initialJobsState, mergeHistoryJobs, sameJobList, sameStringSet, type JobsState } from './jobsState';
import { createTrackedJobsController } from './trackedJobs';

export type { JobsState } from './jobsState';

function createJobsStore() {
  const { subscribe, update } = writable<JobsState>(initialJobsState);
  let state = initialJobsState;
  const trackedJobs = createTrackedJobsController();
  let historyRequestSeq = 0;

  subscribe((value) => {
    state = value;
  });

  function applyActiveJobs(jobs: GenerateJobStatus[]) {
    // Observe every status sighting for completion notifications; the first
    // snapshot only primes the tracking (see observeJobStatusChange).
    jobs.forEach((job) => observeJobStatusChange(job));
    // Every tracked job (not just one) converges from an accepted snapshot.
    jobs.forEach((job) => trackedJobs.applyFromFeed(job));
    const activeIds = new Set(jobs.map((job) => job.job_id));
    const selectedIds = new Set([...state.selectedIds].filter((id) => activeIds.has(id)));
    update((current) => {
      if (sameJobList(current.jobs, jobs) && sameStringSet(current.selectedIds, selectedIds)) return current;
      return { ...current, jobs, selectedIds };
    });
  }

  const jobsFeed = createJobsFeedController({
    onSnapshot: applyActiveJobs,
    onJob: (job) => {
      // Global events include jobs this tab never tracked.
      observeJobStatusChange(job);
      trackedJobs.applyFromFeed(job);
    },
    onVisible: () => {
      trackedJobs.resumePolling();
    }
  });
  const loadJobs = jobsFeed.load;
  const startJobsEvents = jobsFeed.startEvents;

  async function loadJobHistory(options: { append?: boolean; failedOnly?: boolean } = {}) {
    if (state.historyLoading) return;
    const failedOnly = options.failedOnly ?? state.historyFailedOnly;
    const append = Boolean(options.append) && state.historyFailedOnly === failedOnly;
    const filterChanged = state.historyFailedOnly !== failedOnly;
    const cursorJob = append ? state.historyJobs[state.historyJobs.length - 1] : null;
    const seq = ++historyRequestSeq;
    update((current) => ({
      ...current,
      historyFailedOnly: failedOnly,
      historyLoading: true,
      ...(filterChanged ? { historyJobs: [], historyLoaded: false, historyHasMore: false } : {})
    }));
    try {
      const params = new URLSearchParams({
        include_finished: 'true',
        limit: String(HISTORY_PAGE_SIZE)
      });
      if (append && cursorJob?.updated_at && cursorJob.job_id) {
        params.set('before_updated_at', cursorJob.updated_at);
        params.set('before_job_id', cursorJob.job_id);
      }
      if (failedOnly) params.set('failed_only', 'true');
      const historyJobs = await apiFetch<GenerateJobStatus[]>(`/api/generate/jobs?${params.toString()}`, {}, 'loading job history');
      if (seq !== historyRequestSeq) return;
      update((current) => {
        const mergedJobs = append ? mergeHistoryJobs(current.historyJobs, historyJobs) : historyJobs;
        if (
          sameJobList(current.historyJobs, mergedJobs) &&
          current.historyLoaded &&
          current.historyFailedOnly === failedOnly &&
          current.historyHasMore === (historyJobs.length === HISTORY_PAGE_SIZE) &&
          !filterChanged
        ) {
          return { ...current, historyLoading: false, historyNeedsRefresh: false };
        }
        return {
          ...current,
          historyJobs: mergedJobs,
          historyLoaded: true,
          historyFailedOnly: failedOnly,
          historyHasMore: historyJobs.length === HISTORY_PAGE_SIZE,
          historyNeedsRefresh: append ? current.historyNeedsRefresh : false
        };
      });
    } catch {
      if (seq !== historyRequestSeq) return;
      update((current) => ({
        ...current,
        historyJobs: append ? current.historyJobs : [],
        historyLoaded: true,
        historyFailedOnly: failedOnly,
        historyHasMore: false
      }));
    } finally {
      if (seq === historyRequestSeq) update((current) => ({ ...current, historyLoading: false }));
    }
  }

  async function loadMoreJobHistory() {
    if (!state.historyHasMore || state.historyLoading) return;
    await loadJobHistory({ append: true, failedOnly: state.historyFailedOnly });
  }

  async function refreshHistoryIfLoaded() {
    if (!state.historyLoaded || !state.historyNeedsRefresh) return;
    await loadJobHistory({ failedOnly: state.historyFailedOnly });
  }

  async function setHistoryFailedOnly(failedOnly: boolean) {
    if (state.historyFailedOnly === failedOnly && state.historyLoaded) {
      if (state.historyNeedsRefresh) await refreshHistoryIfLoaded();
      return;
    }
    await loadJobHistory({ failedOnly });
  }

  function markHistoryStale() {
    if (state.historyNeedsRefresh) return;
    update((current) => ({ ...current, historyNeedsRefresh: true }));
  }

  function shouldRefreshJobsAfterSubmit() {
    return !jobsFeed.healthy;
  }

  async function clearJobHistory() {
    const seq = ++historyRequestSeq;
    update((current) => ({
      ...current,
      historyLoading: true,
      historyNeedsRefresh: false
    }));
    try {
      await apiFetch('/api/generate/jobs/history', { method: 'DELETE' }, 'clearing job history');
      if (seq !== historyRequestSeq) return;
      update((current) => ({
        ...current,
        historyJobs: [],
        historyLoaded: true,
        historyHasMore: false,
        historyLoading: false,
        historyNeedsRefresh: false
      }));
    } catch (error) {
      if (seq === historyRequestSeq) update((current) => ({ ...current, historyLoading: false }));
      throw error;
    }
  }

  function toggleSelection(jobId: string) {
    const selectedIds = new Set(state.selectedIds);
    if (selectedIds.has(jobId)) selectedIds.delete(jobId);
    else selectedIds.add(jobId);
    update((current) => ({ ...current, selectedIds }));
  }

  function toggleAll() {
    const selectedIds = state.selectedIds.size === state.jobs.length ? new Set<string>() : new Set(state.jobs.map((job) => job.job_id));
    update((current) => ({ ...current, selectedIds }));
  }

  async function cancelSelected() {
    const ids = [...state.selectedIds];
    await Promise.all(
      ids.map((jobId) =>
        apiFetch(`/api/generate/${encodeURIComponent(jobId)}`, { method: 'DELETE' }, 'cancelling job').catch(() => null)
      )
    );
    update((current) => ({ ...current, selectedIds: new Set() }));
    await loadJobs();
    await refreshHistoryIfLoaded();
  }

  function trackJob(
    jobId: string,
    updatePreviewFromJob: (job: GenerateJobStatus) => void | Promise<void>,
    setPreviewError: (message: string) => void,
    onPreview?: (event: GeneratePreviewEvent) => void
  ) {
    if (!jobId) return;
    jobsFeed.activate();
    // Tracking is per job: a new job never closes an in-flight job's stream.
    trackedJobs.track(jobId, {
      onJob: updatePreviewFromJob,
      onError: setPreviewError,
      onPreview
    });
  }

  /** Stop tracking one job (its own resources only). */
  function untrackJob(jobId: string) {
    trackedJobs.release(jobId);
  }

  /** Stop tracking every job: clear, group replacement and cleanup. */
  function closeActiveJobSource() {
    trackedJobs.releaseAll();
  }

  function cleanup() {
    jobsFeed.dispose();
    trackedJobs.releaseAll();
    historyRequestSeq += 1;
  }

  return {
    subscribe,
    loadJobs,
    loadJobHistory,
    loadMoreJobHistory,
    refreshHistoryIfLoaded,
    setHistoryFailedOnly,
    markHistoryStale,
    shouldRefreshJobsAfterSubmit,
    clearJobHistory,
    startJobsEvents,
    toggleSelection,
    toggleAll,
    cancelSelected,
    trackJob,
    untrackJob,
    closeActiveJobSource,
    cleanup
  };
}

export const jobsStore = createJobsStore();
