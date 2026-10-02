import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createCompletionNotificationTracker,
  notificationPermission,
  notificationsSupported
} from '$lib/utils/completionNotifications';
import { preferencesStore } from '$lib/stores/preferences';
import type { GenerateJobStatus } from '$lib/api/types/jobs';

type RecordedNotification = { title: string; body: string; tag?: string };

let constructed: RecordedNotification[] = [];

class StubNotification {
  static permission = 'granted';
  onclick: (() => void) | null = null;
  constructor(
    public title: string,
    public options?: { body?: string; tag?: string }
  ) {
    constructed.push({ title, body: options?.body || '', tag: options?.tag });
  }
  close() {}
}

function job(overrides: Partial<GenerateJobStatus>): GenerateJobStatus {
  return {
    job_id: 'job-1',
    status: 'running',
    prompt: 'a red cube',
    ...overrides
  } as GenerateJobStatus;
}

beforeEach(() => {
  constructed = [];
  vi.stubGlobal('Notification', StubNotification);
  vi.stubGlobal('window', { isSecureContext: true, Notification: StubNotification, focus: () => {} });
  preferencesStore.patch({ taskCompletionNotifications: false });
  return () => {
    vi.unstubAllGlobals();
    preferencesStore.patch({ taskCompletionNotifications: false });
  };
});

describe('completion notifications', () => {
  it('notifies once when a tracked job transitions to a terminal state', () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const { observeJobStatusChange } = createCompletionNotificationTracker();

    observeJobStatusChange(job({ job_id: 'job-a', status: 'running' }));
    observeJobStatusChange(job({ job_id: 'job-a', status: 'success' }));
    expect(constructed).toHaveLength(1);
    expect(constructed[0].title).toBe('Image ready');
    expect(constructed[0].body).toBe('a red cube');
    expect(constructed[0].tag).toBe('gpt-image-panel-job-job-a');

    // SSE replays and polls must not fire a second notification.
    observeJobStatusChange(job({ job_id: 'job-a', status: 'success' }));
    observeJobStatusChange(job({ job_id: 'job-a', status: 'success' }));
    expect(constructed).toHaveLength(1);
  });

  it('does not notify for jobs first seen in a terminal state', () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const { observeJobStatusChange } = createCompletionNotificationTracker();

    // History/reload sightings: terminal without a prior active observation.
    observeJobStatusChange(job({ job_id: 'job-b', status: 'success' }));
    expect(constructed).toHaveLength(0);
  });

  it('does nothing while the preference is off', () => {
    const { observeJobStatusChange } = createCompletionNotificationTracker();
    observeJobStatusChange(job({ job_id: 'job-c', status: 'running' }));
    observeJobStatusChange(job({ job_id: 'job-c', status: 'success' }));
    expect(constructed).toHaveLength(0);
  });

  it('summarises partial failures with counts and reports failures', () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const { observeJobStatusChange } = createCompletionNotificationTracker();

    observeJobStatusChange(job({ job_id: 'job-d', status: 'running' }));
    observeJobStatusChange(job({ job_id: 'job-d', status: 'partial_failure', success_count: 2, failure_count: 1 }));
    expect(constructed).toHaveLength(1);
    expect(constructed[0].title).toBe('Task finished with failures');
    expect(constructed[0].body).toContain('2 succeeded, 1 failed');

    observeJobStatusChange(job({ job_id: 'job-e', status: 'running' }));
    observeJobStatusChange(job({ job_id: 'job-e', status: 'upstream_error', error: 'quota exhausted' }));
    expect(constructed).toHaveLength(2);
    expect(constructed[1].title).toBe('Task failed');
  });

  it('exposes permission helpers backed by the Notification API', () => {
    expect(notificationsSupported()).toBe(true);
    expect(notificationPermission()).toBe('granted');
  });
});
