/**
 * Per-job tracking resources for the current preview group.
 *
 * Each tracked job owns its own lifecycle epoch, fallback polling timer, and
 * in-flight poll token. Per-job SSE (`/api/generate/{id}/events`) is opened
 * only for active, non-queued streaming jobs (`streaming: true`) that emit
 * partial preview frames; non-streaming and queued jobs rely on the single
 * global `/api/generate/jobs/events` feed with per-job polling fallback when
 * the global feed is unhealthy.
 */
import { get } from 'svelte/store';
import { ApiError } from '$lib/api/client';
import { fetchGenerateJob, openGenerateJobEvents } from '$lib/api/jobs';
import { t } from '$lib/i18n';
import { isActiveJobStatus } from '$lib/utils/jobs';
import { isPageVisible } from '$lib/utils/network';
import type { GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';

export type TrackedJobHandlers = {
  onJob: (job: GenerateJobStatus) => void | Promise<void>;
  onError: (message: string) => void;
  onPreview?: (event: GeneratePreviewEvent) => void;
};

export type TrackJobOptions = {
  streaming?: boolean;
  status?: string;
  feedHealthy?: () => boolean;
};

type Tracker = {
  jobId: string;
  handlers: TrackedJobHandlers;
  streaming: boolean;
  status: string;
  feedHealthy?: () => boolean;
  /** Invalidated before teardown so late callbacks cannot revive the tracker. */
  epoch: number;
  source: EventSource | null;
  healthy: boolean;
  timer: ReturnType<typeof setTimeout> | null;
  pollToken: object | null;
};

export const TRACKED_JOB_POLL_INTERVAL_MS = 1200;
export const MAX_TRACKED_JOBS = 8;

export function createTrackedJobsController() {
  const trackers = new Map<string, Tracker>();

  function isCurrent(tracker: Tracker, epoch: number) {
    return trackers.get(tracker.jobId) === tracker && tracker.epoch === epoch;
  }

  function clearTimer(tracker: Tracker) {
    if (tracker.timer) clearTimeout(tracker.timer);
    tracker.timer = null;
  }

  function closeSource(tracker: Tracker) {
    // Invalidate before close in case the transport delivers a final callback.
    tracker.epoch += 1;
    const previous = tracker.source;
    tracker.source = null;
    tracker.healthy = false;
    previous?.close();
  }

  function shouldOpenSource(tracker: Tracker) {
    return tracker.streaming && isActiveJobStatus(tracker.status) && tracker.status !== 'queued';
  }

  function needsFallbackPoll(tracker: Tracker) {
    return tracker.source ? !tracker.healthy : !tracker.feedHealthy?.();
  }

  function release(jobId: string) {
    const tracker = trackers.get(jobId);
    if (!tracker) return;
    trackers.delete(jobId);
    tracker.epoch += 1;
    const previous = tracker.source;
    tracker.source = null;
    tracker.healthy = false;
    if (tracker.timer) clearTimeout(tracker.timer);
    tracker.timer = null;
    tracker.pollToken = null;
    previous?.close();
  }

  function releaseAll() {
    for (const jobId of [...trackers.keys()]) release(jobId);
  }

  async function apply(tracker: Tracker, job: GenerateJobStatus) {
    const epoch = tracker.epoch;
    tracker.status = job.status;
    if (job.streaming !== undefined) tracker.streaming = Boolean(job.streaming);
    try {
      await tracker.handlers.onJob(job);
    } finally {
      if (isCurrent(tracker, epoch) && !isActiveJobStatus(job.status)) {
        release(tracker.jobId);
      }
    }
  }

  /** Apply a snapshot that arrived through the global feed. */
  function applyFromFeed(job: GenerateJobStatus) {
    const tracker = trackers.get(job.job_id);
    if (!tracker) return;
    tracker.status = job.status;
    if (job.streaming !== undefined) tracker.streaming = Boolean(job.streaming);
    clearTimer(tracker);
    if (!tracker.source && shouldOpenSource(tracker)) {
      openSource(tracker);
    }
    void apply(tracker, job);
  }

  function schedulePoll(tracker: Tracker) {
    if (tracker.timer || trackers.get(tracker.jobId) !== tracker) return;
    if (!isPageVisible()) return;
    const epoch = tracker.epoch;
    tracker.timer = setTimeout(() => {
      tracker.timer = null;
      if (isCurrent(tracker, epoch)) void poll(tracker);
    }, TRACKED_JOB_POLL_INTERVAL_MS);
  }

  async function poll(tracker: Tracker) {
    if (trackers.get(tracker.jobId) !== tracker || tracker.pollToken) return;
    if (!isPageVisible()) return;
    clearTimer(tracker);
    const token = {};
    const epoch = tracker.epoch;
    tracker.pollToken = token;
    try {
      const job = await fetchGenerateJob(tracker.jobId, 'loading job');
      if (!isCurrent(tracker, epoch) || tracker.pollToken !== token) return;
      await apply(tracker, job);
      if (!isCurrent(tracker, epoch)) return;
      if (isActiveJobStatus(job.status)) {
        if (!tracker.source && shouldOpenSource(tracker)) openSource(tracker);
        if (needsFallbackPoll(tracker)) schedulePoll(tracker);
      }
    } catch (error) {
      if (!isCurrent(tracker, epoch) || tracker.pollToken !== token) return;
      const permanentError = error instanceof ApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status);
      if (permanentError) {
        tracker.handlers.onError(error.message || get(t).messages.jobLoadFailed);
        release(tracker.jobId);
      } else if (needsFallbackPoll(tracker)) {
        schedulePoll(tracker);
      }
    } finally {
      // An old request must never unlock a newer lifecycle's in-flight token.
      if (tracker.pollToken === token) tracker.pollToken = null;
    }
  }

  function startPolling(tracker: Tracker) {
    if (tracker.timer || tracker.pollToken) return;
    if (!isPageVisible()) return;
    void poll(tracker);
  }

  function openSource(tracker: Tracker) {
    if (tracker.source) return;
    const epoch = tracker.epoch;
    const source = openGenerateJobEvents(tracker.jobId, {
      onEvent: ({ event, data }) => {
        if (!isCurrent(tracker, epoch) || tracker.source !== source) return;
        if (event === 'preview') {
          const previewEvent = data as GeneratePreviewEvent;
          if (previewEvent.job_id !== tracker.jobId) return;
          tracker.handlers.onPreview?.(previewEvent);
          return;
        }
        const jobData = data as GenerateJobStatus;
        if (jobData.job_id !== tracker.jobId) return;
        tracker.healthy = true;
        clearTimer(tracker);
        void apply(tracker, jobData);
      },
      onNetworkError: () => {
        if (!isCurrent(tracker, epoch) || tracker.source !== source) return;
        tracker.healthy = false;
        startPolling(tracker);
      },
      onError: () => {
        if (!isCurrent(tracker, epoch) || tracker.source !== source) return;
        closeSource(tracker);
        startPolling(tracker);
      }
    });
    tracker.source = source;
  }

  /** Track a job once; re-tracking the same id refreshes its handlers and streaming state. */
  function track(jobId: string, handlers: TrackedJobHandlers, options: TrackJobOptions = {}) {
    if (!jobId) return;
    const existing = trackers.get(jobId);
    if (existing) {
      existing.handlers = handlers;
      if (options.streaming !== undefined) existing.streaming = Boolean(options.streaming);
      if (options.status) existing.status = options.status;
      if (options.feedHealthy) existing.feedHealthy = options.feedHealthy;
      if (!existing.source && shouldOpenSource(existing)) openSource(existing);
      return;
    }
    if (trackers.size >= MAX_TRACKED_JOBS) {
      // Never drop silently: the job keeps running server-side (see the Jobs drawer).
      handlers.onError(get(t).messages.tooManyActiveSubmissions(MAX_TRACKED_JOBS));
      return;
    }
    const tracker: Tracker = {
      jobId,
      handlers,
      streaming: Boolean(options.streaming),
      status: options.status || 'queued',
      feedHealthy: options.feedHealthy,
      epoch: 0,
      source: null,
      healthy: false,
      timer: null,
      pollToken: null
    };
    trackers.set(jobId, tracker);
    if (shouldOpenSource(tracker)) {
      openSource(tracker);
    } else if (needsFallbackPoll(tracker)) {
      schedulePoll(tracker);
    }
  }

  /**
   * The global feed recovered. A reconnect snapshot lists only active jobs, so a
   * feed-backed job that finished during the outage would never be seen again:
   * confirm each one with a single poll instead of just dropping its timer.
   */
  function onFeedHealthy() {
    for (const tracker of [...trackers.values()]) {
      if (tracker.source) continue;
      clearTimer(tracker);
      void poll(tracker);
    }
  }

  /** Start fallback polling for feed-backed trackers when the global feed drops. */
  function onFeedUnhealthy() {
    for (const tracker of trackers.values()) {
      if (!tracker.source && !tracker.timer && !tracker.pollToken) {
        schedulePoll(tracker);
      }
    }
  }

  /** Restart fallback polling for every unhealthy tracker (page became visible). */
  function resumePolling() {
    for (const tracker of trackers.values()) {
      if (tracker.healthy || tracker.timer || tracker.pollToken) continue;
      if (!isPageVisible()) return;
      void poll(tracker);
    }
  }

  return {
    track,
    release,
    releaseAll,
    applyFromFeed,
    onFeedHealthy,
    onFeedUnhealthy,
    resumePolling,
    get size() {
      return trackers.size;
    },
    get healthy() {
      for (const tracker of trackers.values()) if (tracker.healthy) return true;
      return false;
    }
  };
}

export type TrackedJobsController = ReturnType<typeof createTrackedJobsController>;
