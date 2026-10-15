/** Owns global job connections and requests; store state is applied by callbacks. */
import { apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { isPageVisible } from '$lib/utils/network';

type JobsFeedHandlers = {
  onSnapshot: (jobs: GenerateJobStatus[]) => void;
  onJob: (job: GenerateJobStatus) => void;
  onVisible: () => void;
};

export function createJobsFeedController(handlers: JobsFeedHandlers) {
  let active = false;
  let epoch = 0;
  let requestSeq = 0;
  let snapshotRevision = 0;
  let source: EventSource | null = null;
  let healthy = false;
  let pollingTimer: ReturnType<typeof setInterval> | null = null;
  let pollInFlight: object | null = null;

  function handleVisibilityChange() {
    if (!active || document.visibilityState !== 'visible') return;
    // Converge once after hidden-tab polling suspension, even with a live SSE.
    void load();
    handlers.onVisible();
  }

  /** Load, connect and tracked-only use can reactivate the singleton after disposal. */
  function activate() {
    if (active) return;
    active = true;
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibilityChange);
    }
  }

  async function load() {
    activate();
    const mine = epoch;
    const seq = ++requestSeq;
    const revision = snapshotRevision;
    try {
      const jobs = await apiFetch<GenerateJobStatus[]>('/api/generate/jobs', {}, 'loading jobs');
      if (!active || epoch !== mine || seq !== requestSeq || revision !== snapshotRevision) return;
      snapshotRevision += 1;
      handlers.onSnapshot(jobs);
    } catch {
      // Keep the last successful snapshot on transient failures.
    }
  }

  function pollIfVisible() {
    if (!active || !isPageVisible() || pollInFlight) return;
    const token = {};
    pollInFlight = token;
    void load().finally(() => {
      // A disposed/replaced poll must not unlock a newer in-flight request.
      if (pollInFlight === token) pollInFlight = null;
    });
  }

  function startPolling() {
    if (pollingTimer !== null) return;
    pollIfVisible();
    pollingTimer = setInterval(pollIfVisible, 5000);
  }

  function stopPolling() {
    if (pollingTimer !== null) clearInterval(pollingTimer);
    pollingTimer = null;
  }

  function closeSource() {
    // Invalidate before close in case a transport delivers a final callback.
    epoch += 1;
    const previous = source;
    source = null;
    healthy = false;
    pollInFlight = null;
    previous?.close();
  }

  function startEvents() {
    activate();
    closeSource();
    const mine = epoch;
    const next = openJsonEventSource<GenerateJobStatus[] | GenerateJobStatus>('/api/generate/jobs/events', {
      onEvent: ({ event, data }) => {
        if (!isCurrent()) return;
        stopPolling();
        healthy = true;
        if (event === 'jobs' && Array.isArray(data)) {
          snapshotRevision += 1;
          handlers.onSnapshot(data);
        } else if (event === 'job' && !Array.isArray(data)) {
          snapshotRevision += 1;
          handlers.onJob(data);
        }
      },
      onNetworkError: handleError,
      onError: handleError
    }, ['jobs', 'job']);

    function isCurrent() {
      return active && epoch === mine && source === next;
    }
    function handleError() {
      if (!isCurrent()) return;
      healthy = false;
      startPolling();
    }
    next.onopen = () => {
      if (!isCurrent()) return;
      healthy = true;
      stopPolling();
    };
    source = next;
  }

  function dispose() {
    active = false;
    closeSource();
    stopPolling();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    }
  }

  return { activate, load, startEvents, dispose, get healthy() { return healthy; } };
}
