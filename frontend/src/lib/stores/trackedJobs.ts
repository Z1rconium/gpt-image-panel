/**
 * Per-job tracking resources for the current preview group.
 *
 * Each tracked job owns its own SSE source, lifecycle epoch, fallback polling
 * timer and in-flight token, so starting or finishing one job never closes
 * another job's stream. The global jobs feed stays a single connection; its
 * `job` events are dispatched to the matching tracker here.
 */
import { get } from 'svelte/store';
import { ApiError, apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';
import { isActiveJobStatus } from '$lib/utils/jobs';
import { isPageVisible } from '$lib/utils/network';
import type { GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';

export type TrackedJobHandlers = {
  onJob: (job: GenerateJobStatus) => void | Promise<void>;
  onError: (message: string) => void;
  onPreview?: (event: GeneratePreviewEvent) => void;
};

type Tracker = {
  jobId: string;
  handlers: TrackedJobHandlers;
  /** Invalidated before teardown so late callbacks cannot revive the tracker. */
  epoch: number;
  source: EventSource | null;
  healthy: boolean;
  timer: ReturnType<typeof setTimeout> | null;
  pollToken: object | null;
};

export const TRACKED_JOB_POLL_INTERVAL_MS = 1200;

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
    await tracker.handlers.onJob(job);
    if (!isCurrent(tracker, epoch)) return;
    if (!isActiveJobStatus(job.status)) release(tracker.jobId);
  }

  /** Apply a snapshot that arrived through the global feed. */
  function applyFromFeed(job: GenerateJobStatus) {
    const tracker = trackers.get(job.job_id);
    if (!tracker) return;
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
      const job = await apiFetch<GenerateJobStatus>(
        `/api/generate/${encodeURIComponent(tracker.jobId)}`,
        {},
        'loading job'
      );
      if (!isCurrent(tracker, epoch) || tracker.pollToken !== token) return;
      await apply(tracker, job);
      if (!isCurrent(tracker, epoch)) return;
      if (isActiveJobStatus(job.status)) {
        if (!tracker.healthy) schedulePoll(tracker);
      } else {
        closeSource(tracker);
      }
    } catch (error) {
      if (!isCurrent(tracker, epoch) || tracker.pollToken !== token) return;
      const permanentError = error instanceof ApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status);
      if (permanentError) {
        tracker.handlers.onError(error.message || get(t).messages.jobLoadFailed);
        release(tracker.jobId);
      } else {
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
    const epoch = tracker.epoch;
    const source = openJsonEventSource<GenerateJobStatus | GeneratePreviewEvent>(
      `/api/generate/${encodeURIComponent(tracker.jobId)}/events`,
      {
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
      },
      ['job', 'preview']
    );
    tracker.source = source;
  }

  /** Track a job once; re-tracking the same id only refreshes its handlers. */
  function track(jobId: string, handlers: TrackedJobHandlers) {
    if (!jobId) return;
    const existing = trackers.get(jobId);
    if (existing) {
      existing.handlers = handlers;
      return;
    }
    const tracker: Tracker = {
      jobId,
      handlers,
      epoch: 0,
      source: null,
      healthy: false,
      timer: null,
      pollToken: null
    };
    trackers.set(jobId, tracker);
    openSource(tracker);
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
