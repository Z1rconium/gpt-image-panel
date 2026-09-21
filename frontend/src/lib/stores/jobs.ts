import { get, writable } from 'svelte/store';
import { ApiError, apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';
import { filenameFromImageUrl, jobFailureMessage } from '$lib/utils/format';
import { isPageVisible } from '$lib/utils/network';
import { isActiveJobStatus } from '$lib/utils/jobs';
import type { GeneratePreviewEvent, GenerateJobResponse, GenerateJobStatus } from '$lib/api/types/jobs';
import type { PreviewState } from '$lib/stores/preview';

export type JobsState = {
  jobs: GenerateJobStatus[];
  historyJobs: GenerateJobStatus[];
  historyLoading: boolean;
  historyLoaded: boolean;
  historyNeedsRefresh: boolean;
  historyHasMore: boolean;
  historyFailedOnly: boolean;
  selectedIds: Set<string>;
};

const initialJobsState: JobsState = {
  jobs: [],
  historyJobs: [],
  historyLoading: false,
  historyLoaded: false,
  historyNeedsRefresh: false,
  historyHasMore: false,
  historyFailedOnly: false,
  selectedIds: new Set()
};

const HISTORY_PAGE_SIZE = 50;
const HISTORY_CACHE_LIMIT = 500;

export type JobMaskFetch = {
  blob: Blob;
  /** From the X-Mask-Coverage response header; null if the server omitted it. */
  coverage: number | null;
};

/**
 * Fetch the mask persisted for a finished edit job. Mask files are named after
 * their job, so this needs only the job id. The server already knows the
 * coverage (computed once at admission time), so it rides along as a response
 * header instead of the caller re-deriving it from a full-canvas decode.
 */
export async function fetchJobMaskBlob(jobId: string): Promise<JobMaskFetch> {
  let response: Response;
  try {
    response = await fetch(`/api/generate/${encodeURIComponent(jobId)}/mask`, {
      credentials: 'same-origin',
      headers: { Accept: 'image/png' }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : get(t).messages.failedToFetch;
    throw new Error(get(t).messages.networkError(message));
  }
  if (!response.ok) {
    const message = response.status === 404 ? get(t).messages.editRetryMaskMissing : get(t).messages.requestFailed;
    throw new ApiError(message, response.status, null, 'loading job mask');
  }
  const coverageHeader = response.headers.get('X-Mask-Coverage');
  const coverage = coverageHeader !== null ? Number(coverageHeader) : null;
  return {
    blob: await response.blob(),
    coverage: coverage !== null && Number.isFinite(coverage) ? coverage : null
  };
}

function sameJobImage(
  left: NonNullable<GenerateJobStatus['images']>[number],
  right: NonNullable<GenerateJobStatus['images']>[number]
) {
  return (
    left.image_id === right.image_id &&
    left.image_url === right.image_url &&
    left.filename === right.filename &&
    left.image_width === right.image_width &&
    left.image_height === right.image_height
  );
}

function sameJobImages(left: GenerateJobStatus['images'], right: GenerateJobStatus['images']) {
  const leftImages = left || [];
  const rightImages = right || [];
  if (leftImages.length !== rightImages.length) return false;
  for (let index = 0; index < leftImages.length; index += 1) {
    const leftImage = leftImages[index];
    const rightImage = rightImages[index];
    if (!rightImage || !sameJobImage(leftImage, rightImage)) return false;
  }
  return true;
}

function sameStageTimings(left: GenerateJobStatus['stage_timings'], right: GenerateJobStatus['stage_timings']) {
  const leftTimings = left || {};
  const rightTimings = right || {};
  const leftKeys = Object.keys(leftTimings);
  const rightKeys = Object.keys(rightTimings);
  if (leftKeys.length !== rightKeys.length) return false;
  for (const key of leftKeys) {
    if (leftTimings[key] !== rightTimings[key]) return false;
  }
  return true;
}

function sameJob(left: GenerateJobStatus, right: GenerateJobStatus) {
  return (
    left.job_id === right.job_id &&
    left.status === right.status &&
    left.stage === right.stage &&
    left.message === right.message &&
    left.operation === right.operation &&
    left.updated_at === right.updated_at &&
    left.completed_at === right.completed_at &&
    left.image_id === right.image_id &&
    left.image_url === right.image_url &&
    left.prompt === right.prompt &&
    left.size === right.size &&
    left.model === right.model &&
    left.completed_count === right.completed_count &&
    left.success_count === right.success_count &&
    left.failure_count === right.failure_count &&
    left.duration === right.duration &&
    left.error === right.error &&
    sameJobImages(left.images, right.images) &&
    sameStageTimings(left.stage_timings, right.stage_timings)
  );
}

function sameJobList(left: GenerateJobStatus[], right: GenerateJobStatus[]) {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    const current = left[index];
    const next = right[index];
    if (!next || !sameJob(current, next)) return false;
  }
  return true;
}

function sameStringSet(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}

function mergeHistoryJobs(currentJobs: GenerateJobStatus[], nextJobs: GenerateJobStatus[]) {
  const merged: GenerateJobStatus[] = [];
  const seen = new Set<string>();
  for (const job of [...currentJobs, ...nextJobs]) {
    if (seen.has(job.job_id)) continue;
    seen.add(job.job_id);
    merged.push(job);
    if (merged.length >= HISTORY_CACHE_LIMIT) break;
  }
  return merged;
}

function createJobsStore() {
  const { subscribe, update } = writable<JobsState>(initialJobsState);
  let state = initialJobsState;
  let jobsSource: EventSource | null = null;
  let jobsPollingTimer: ReturnType<typeof setInterval> | null = null;
  let jobsPollInFlight = false;
  let jobsFeedHealthy = false;
  let activeJobSource: EventSource | null = null;
  let activeJobFeedHealthy = false;
  let activeJobPollingTimer: ReturnType<typeof setTimeout> | null = null;
  const activeJobPollsInFlight = new Set<number>();
  let trackedJobId: string | null = null;
  let trackedJobUpdate: ((job: GenerateJobStatus) => Promise<void>) | null = null;
  let trackedJobError: ((message: string) => void) | null = null;
  let trackedJobPreview: ((event: GeneratePreviewEvent) => void) | null = null;
  let trackedJobGeneration = 0;
  let historyRequestSeq = 0;

  subscribe((value) => {
    state = value;
  });

  function applyActiveJobs(jobs: GenerateJobStatus[]) {
    const trackedJob = trackedJobId ? jobs.find((job) => job.job_id === trackedJobId) : null;
    if (trackedJob) applyTrackedJob(trackedJob);
    const activeIds = new Set(jobs.map((job) => job.job_id));
    const selectedIds = new Set([...state.selectedIds].filter((id) => activeIds.has(id)));
    update((current) => {
      if (sameJobList(current.jobs, jobs) && sameStringSet(current.selectedIds, selectedIds)) return current;
      return { ...current, jobs, selectedIds };
    });
  }

  async function loadJobs() {
    try {
      const jobs = await apiFetch<GenerateJobStatus[]>('/api/generate/jobs', {}, 'loading jobs');
      applyActiveJobs(jobs);
    } catch {
      // Keep the last successful snapshot on transient failures.
    }
  }

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
    return !jobsFeedHealthy;
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

  function loadJobsIfVisible() {
    // Background tabs must not burn the fallback poll; the visibilitychange
    // handler refreshes once when the page becomes visible again.
    if (!isPageVisible() || jobsPollInFlight) return;
    jobsPollInFlight = true;
    void loadJobs().finally(() => {
      jobsPollInFlight = false;
    });
  }

  function startJobsPolling() {
    if (jobsPollingTimer) return;
    void loadJobsIfVisible();
    jobsPollingTimer = setInterval(() => {
      void loadJobsIfVisible();
    }, 5000);
  }

  function stopJobsPolling() {
    if (jobsPollingTimer) clearInterval(jobsPollingTimer);
    jobsPollingTimer = null;
  }

  function startJobsEvents() {
    jobsSource?.close();
    jobsFeedHealthy = false;
    const source = openJsonEventSource<GenerateJobStatus[] | GenerateJobStatus>('/api/generate/jobs/events', {
      onEvent: ({ event, data }) => {
        stopJobsPolling();
        jobsFeedHealthy = true;
        if (event === 'jobs' && Array.isArray(data)) {
          applyActiveJobs(data);
        } else if (event === 'job' && !Array.isArray(data)) {
          void applyTrackedJob(data);
        }
      },
      onNetworkError: () => {
        jobsFeedHealthy = false;
        startJobsPolling();
      },
      onError: () => {
        jobsFeedHealthy = false;
        startJobsPolling();
      }
    }, ['jobs', 'job']);
    source.onopen = () => {
      if (jobsSource !== source) return;
      jobsFeedHealthy = true;
      stopJobsPolling();
    };
    jobsSource = source;
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
    updatePreviewFromJob: (job: GenerateJobStatus) => Promise<void>,
    setPreviewError: (message: string) => void,
    onPreview?: (event: GeneratePreviewEvent) => void
  ) {
    if (!jobId) return;
    closeActiveJobSource();
    trackedJobId = jobId;
    trackedJobUpdate = updatePreviewFromJob;
    trackedJobError = setPreviewError;
    trackedJobPreview = onPreview || null;
    const generation = trackedJobGeneration;
    const source = openJsonEventSource<GenerateJobStatus | GeneratePreviewEvent>(
      `/api/generate/${encodeURIComponent(jobId)}/events`,
      {
        onEvent: ({ event, data }) => {
          if (activeJobSource !== source || trackedJobId !== jobId || trackedJobGeneration !== generation) return;
          if (event === 'preview') {
            const previewEvent = data as GeneratePreviewEvent;
            if (previewEvent.job_id !== jobId) return;
            trackedJobPreview?.(previewEvent);
            return;
          }
          const jobData = data as GenerateJobStatus;
          if (jobData.job_id !== jobId) return;
          activeJobFeedHealthy = true;
          clearActiveJobPollingTimer();
          void applyTrackedJob(jobData);
        },
        onNetworkError: () => {
          if (activeJobSource !== source || trackedJobId !== jobId || trackedJobGeneration !== generation) return;
          activeJobFeedHealthy = false;
          startTrackedJobPolling();
        },
        onError: () => {
          if (activeJobSource !== source || trackedJobId !== jobId || trackedJobGeneration !== generation) return;
          closeActiveJobEventSource();
          startTrackedJobPolling();
        }
      },
      ['job', 'preview']
    );
    activeJobSource = source;
  }

  async function applyTrackedJob(job: GenerateJobStatus) {
    if (trackedJobId !== job.job_id || !trackedJobUpdate) return;
    const generation = trackedJobGeneration;
    const updatePreviewFromJob = trackedJobUpdate;
    await updatePreviewFromJob(job);
    if (
      trackedJobId === job.job_id &&
      trackedJobGeneration === generation &&
      !isActiveJobStatus(job.status)
    ) closeActiveJobSource();
  }

  function startTrackedJobPolling() {
    if (
      !trackedJobId ||
      !trackedJobUpdate ||
      !trackedJobError ||
      activeJobPollingTimer ||
      activeJobPollsInFlight.has(trackedJobGeneration)
    ) return;
    void pollJob(trackedJobId, trackedJobGeneration);
  }

  function scheduleTrackedJobPoll(jobId: string, generation: number) {
    if (activeJobPollingTimer || trackedJobId !== jobId || trackedJobGeneration !== generation) return;
    if (!isPageVisible()) return;
    activeJobPollingTimer = setTimeout(() => {
      activeJobPollingTimer = null;
      if (trackedJobId === jobId && trackedJobGeneration === generation) void pollJob(jobId, generation);
    }, 1200);
  }

  async function pollJob(jobId: string, generation: number) {
    if (
      trackedJobId !== jobId ||
      trackedJobGeneration !== generation ||
      activeJobPollsInFlight.has(generation)
    ) return;
    if (!isPageVisible()) return;
    clearActiveJobPollingTimer();
    activeJobPollsInFlight.add(generation);
    try {
      const job = await apiFetch<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}`, {}, 'loading job');
      if (trackedJobId !== jobId || trackedJobGeneration !== generation || !trackedJobUpdate) return;
      await trackedJobUpdate(job);
      if (trackedJobId !== jobId || trackedJobGeneration !== generation) return;
      if (isActiveJobStatus(job.status)) {
        if (!activeJobFeedHealthy) scheduleTrackedJobPoll(jobId, generation);
      } else {
        closeActiveJobSource();
      }
    } catch (error) {
      if (trackedJobId !== jobId || trackedJobGeneration !== generation) return;
      const permanentError = error instanceof ApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status);
      if (permanentError) {
        trackedJobError?.(error.message || get(t).messages.jobLoadFailed);
        closeActiveJobSource();
      } else {
        scheduleTrackedJobPoll(jobId, generation);
      }
    } finally {
      activeJobPollsInFlight.delete(generation);
    }
  }

  function makeQueuedPreview(currentPrompt: string, operation: NonNullable<GenerateJobResponse['operation']>): PreviewState {
    closeActiveJobSource();
    return {
      loading: true,
      error: '',
      imageUrl: '',
      filename: '',
      prompt: currentPrompt,
      streamingPreviewDataUrl: '',
      streamingPreviewSequence: 0,
      job: {
        job_id: '',
        status: 'queued',
        stage: 'queued',
        message: operation === 'edit' ? get(t).messages.queuedEdit : get(t).messages.queuedGeneration,
        operation
      }
    };
  }

  function previewFromJob(job: GenerateJobStatus, preview: PreviewState): PreviewState {
    const primaryImage = job.images?.[0];
    const image = primaryImage?.image_url || job.image_url || '';
    const active = isActiveJobStatus(job.status);
    return {
      loading: active,
      error: jobFailureMessage(job, get(t).messages.jobFailed),
      job,
      imageUrl: image || preview.imageUrl,
      filename: primaryImage?.filename || (image ? filenameFromImageUrl(image) : preview.filename),
      prompt: job.prompt || preview.prompt,
      // The final image always wins over a streamed partial; once it lands,
      // drop the streaming preview so PreviewPanel switches to the real image.
      // A failed job never gets one, so release its (potentially large) data URL.
      streamingPreviewDataUrl: image || !active ? '' : preview.streamingPreviewDataUrl,
      streamingPreviewSequence: image || !active ? 0 : preview.streamingPreviewSequence
    };
  }

  function clearActiveJobPollingTimer() {
    if (activeJobPollingTimer) clearTimeout(activeJobPollingTimer);
    activeJobPollingTimer = null;
  }

  function closeActiveJobEventSource() {
    activeJobSource?.close();
    activeJobSource = null;
    activeJobFeedHealthy = false;
  }

  function closeActiveJobSource() {
    trackedJobGeneration += 1;
    closeActiveJobEventSource();
    clearActiveJobPollingTimer();
    trackedJobId = null;
    trackedJobUpdate = null;
    trackedJobError = null;
    trackedJobPreview = null;
  }

  function handleVisibilityChange() {
    if (typeof document === 'undefined' || document.visibilityState !== 'visible') return;
    // Polling is suspended while hidden, so converge once on return. The SSE
    // feeds stay connected and keep the store current, so only refresh
    // tracked-job data when its own feed is unhealthy.
    void loadJobs();
    if (!activeJobFeedHealthy) startTrackedJobPolling();
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibilityChange);
  }

  function cleanup() {
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    }
    closeActiveJobSource();
    jobsSource?.close();
    jobsSource = null;
    stopJobsPolling();
    jobsFeedHealthy = false;
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
    makeQueuedPreview,
    previewFromJob,
    closeActiveJobSource,
    cleanup
  };
}

export const jobsStore = createJobsStore();
