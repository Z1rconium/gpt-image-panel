import { get } from 'svelte/store';
import { t } from '$lib/i18n';
import { preferencesStore } from '$lib/stores/preferences';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { isActiveJobStatus } from '$lib/utils/jobs';

export type NotificationPermissionState = 'unsupported' | 'default' | 'granted' | 'denied';

const NOTIFIED_JOBS_STORAGE_KEY = 'gpt-image-panel-notified-jobs';
const NOTIFIED_JOBS_LIMIT = 200;
const NOTIFIED_BODY_MAX_CHARS = 160;

/**
 * Browser completion notifications for image tasks. The panel only runs while
 * the page is open (webhooks cover closed-page integrations), so this notifies
 * when a task settles while the page is hidden or unfocused, once per job.
 */
export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window && window.isSecureContext;
}

export function notificationPermission(): NotificationPermissionState {
  if (!notificationsSupported()) return 'unsupported';
  return Notification.permission as NotificationPermissionState;
}

export async function requestNotificationPermission(): Promise<NotificationPermissionState> {
  if (!notificationsSupported()) return 'unsupported';
  try {
    const result = await Notification.requestPermission();
    return result as NotificationPermissionState;
  } catch {
    return 'denied';
  }
}

function loadNotifiedJobIds(): Set<string> {
  try {
    if (typeof localStorage === 'undefined') return new Set();
    const raw = localStorage.getItem(NOTIFIED_JOBS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : []);
  } catch {
    return new Set();
  }
}

function persistNotifiedJobIds(ids: Set<string>) {
  try {
    if (typeof localStorage === 'undefined') return;
    const bounded = [...ids].slice(-NOTIFIED_JOBS_LIMIT);
    localStorage.setItem(NOTIFIED_JOBS_STORAGE_KEY, JSON.stringify(bounded));
  } catch {
    // Storage may be unavailable; in-memory dedupe still applies.
  }
}

function jobNotificationBody(job: GenerateJobStatus): string {
  const prompt = (job.prompt || '').trim();
  if (job.status === 'partial_failure') {
    const succeeded = job.success_count ?? 0;
    const failed = job.failure_count ?? 0;
    return `${get(t).notifications.partialFailureCounts(succeeded, failed)}${prompt ? ` — ${prompt}` : ''}`;
  }
  return prompt.slice(0, NOTIFIED_BODY_MAX_CHARS) || (job.error || '');
}

function showJobNotification(job: GenerateJobStatus) {
  const messages = get(t).notifications;
  const title = job.status === 'success' ? messages.jobSucceeded : job.status === 'partial_failure' ? messages.jobPartialFailure : messages.jobFailed;
  try {
    const notification = new Notification(title, {
      body: jobNotificationBody(job),
      tag: `gpt-image-panel-job-${job.job_id}`,
      data: { job_id: job.job_id }
    });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers throw when constructing without a service worker; the
    // in-page running jobs list remains the fallback.
  }
}

function pageInBackground(): boolean {
  if (typeof document === 'undefined') return true;
  return document.hidden || !document.hasFocus();
}

export type CompletionNotificationTracker = {
  /**
   * Feed every job status sighting through here. Only the first terminal state
   * of a job that was previously seen active notifies, so SSE replays, polling,
   * history loads and reloads never fire duplicate notifications. Cross-tab
   * duplicates are suppressed through localStorage.
   */
  observeJobStatusChange: (job: GenerateJobStatus) => void;
};

export function createCompletionNotificationTracker(): CompletionNotificationTracker {
  const notifiedJobIds = loadNotifiedJobIds();
  const activeJobIds = new Set<string>();

  function observeJobStatusChange(job: GenerateJobStatus) {
    if (!job?.job_id) return;
    if (isActiveJobStatus(job.status)) {
      activeJobIds.add(job.job_id);
      return;
    }
    if (notifiedJobIds.has(job.job_id)) return;
    notifiedJobIds.add(job.job_id);
    persistNotifiedJobIds(notifiedJobIds);
    const wasActive = activeJobIds.delete(job.job_id);
    if (!wasActive) return;
    const preferences = get(preferencesStore);
    if (!preferences.taskCompletionNotifications) return;
    if (notificationPermission() !== 'granted') return;
    // While the user is actively looking at the panel the in-page job list is
    // the notification; the browser one covers background/switched-away use.
    if (!pageInBackground()) return;
    showJobNotification(job);
  }

  return { observeJobStatusChange };
}

const sharedTracker = createCompletionNotificationTracker();

export const observeJobStatusChange = sharedTracker.observeJobStatusChange;

export function notificationPreferenceBlocked(): boolean {
  return notificationPermission() === 'denied';
}
