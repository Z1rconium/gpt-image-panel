import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createCompletionNotificationTracker,
  notificationPermission,
  notificationsSupported,
  requestNotificationPermission
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
  StubNotification.permission = 'granted';
  const entries = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => entries.get(key) ?? null, setItem: (key: string, value: string) => entries.set(key, value), removeItem: (key: string) => entries.delete(key) });
  let lockTail = Promise.resolve();
  vi.stubGlobal('navigator', { locks: { request: (_name: string, callback: () => void) => { const result = lockTail.then(callback); lockTail = result; return result; } } });
  vi.stubGlobal('Notification', StubNotification);
  vi.stubGlobal('window', { isSecureContext: true, Notification: StubNotification, focus: () => {} });
  preferencesStore.patch({ taskCompletionNotifications: false });

});

afterEach(() => { vi.unstubAllGlobals(); preferencesStore.patch({ taskCompletionNotifications: false }); });

describe('completion notifications', () => {
  it('coordinates simultaneous completions across tabs and fresh trackers', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const first = createCompletionNotificationTracker();
    const second = createCompletionNotificationTracker();
    await first.observeJobStatusChange(job({ status: 'running' }));
    await second.observeJobStatusChange(job({ status: 'running' }));
    await Promise.all([first.observeJobStatusChange(job({ status: 'success' })), second.observeJobStatusChange(job({ status: 'success' }))]);
    expect(constructed).toHaveLength(1);
    const reloaded = createCompletionNotificationTracker();
    await reloaded.observeJobStatusChange(job({ status: 'running' }));
    await reloaded.observeJobStatusChange(job({ status: 'success' }));
    expect(constructed).toHaveLength(1);
  });

  it('summarises Agent turns once and excludes their child image jobs', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const tracker = createCompletionNotificationTracker();
    await tracker.observeJobStatusChange(job({ agent_turn_id: 'turn-1', status: 'running' }));
    await tracker.observeJobStatusChange(job({ agent_turn_id: 'turn-1', status: 'success' }));
    const turn = { turnId: 'turn-1', conversationId: 'conversation-1', successCount: 2, failureCount: 1 };
    await tracker.observeAgentTurn({ ...turn, status: 'running' });
    await tracker.observeAgentTurn({ ...turn, status: 'completed' });
    await tracker.observeAgentTurn({ ...turn, status: 'completed' });
    expect(constructed).toHaveLength(1);
    expect(constructed[0].title).toBe('Agent reply complete');
    expect(constructed[0].body).toBe('2 succeeded, 1 failed');
  });

  it('does not request permission or notify with denied or unsupported APIs', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    StubNotification.permission = 'denied';
    const tracker = createCompletionNotificationTracker();
    await tracker.observeJobStatusChange(job({ status: 'running' }));
    await tracker.observeJobStatusChange(job({ status: 'success' }));
    expect(constructed).toHaveLength(0);
    vi.stubGlobal('window', { isSecureContext: false });
    expect(notificationPermission()).toBe('unsupported');
    expect(await requestNotificationPermission()).toBe('unsupported');
  });

  it('bounds partial failure prompts and error bodies', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const tracker = createCompletionNotificationTracker();
    await tracker.observeJobStatusChange(job({ status: 'running' }));
    await tracker.observeJobStatusChange(job({ status: 'partial_failure', prompt: 'x'.repeat(1000), success_count: 2, failure_count: 1 }));
    expect(constructed[0].body.length).toBeLessThanOrEqual(160);
  });
  it('notifies once when a tracked job transitions to a terminal state', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const { observeJobStatusChange } = createCompletionNotificationTracker();

    await observeJobStatusChange(job({ job_id: 'job-a', status: 'running' }));
    await observeJobStatusChange(job({ job_id: 'job-a', status: 'success' }));
    expect(constructed).toHaveLength(1);
    expect(constructed[0].title).toBe('Image ready');
    expect(constructed[0].body).toBe('a red cube');
    expect(constructed[0].tag).toBe('gpt-image-panel-job-job-a');

    // SSE replays and polls must not fire a second notification.
    await observeJobStatusChange(job({ job_id: 'job-a', status: 'success' }));
    await observeJobStatusChange(job({ job_id: 'job-a', status: 'success' }));
    expect(constructed).toHaveLength(1);
  });

  it('does not notify for jobs first seen in a terminal state', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const { observeJobStatusChange } = createCompletionNotificationTracker();

    // History/reload sightings: terminal without a prior active observation.
    await observeJobStatusChange(job({ job_id: 'job-b', status: 'success' }));
    expect(constructed).toHaveLength(0);
  });

  it('does nothing while the preference is off', async () => {
    const { observeJobStatusChange } = createCompletionNotificationTracker();
    await observeJobStatusChange(job({ job_id: 'job-c', status: 'running' }));
    await observeJobStatusChange(job({ job_id: 'job-c', status: 'success' }));
    expect(constructed).toHaveLength(0);
  });

  it('summarises partial failures with counts and reports failures', async () => {
    preferencesStore.patch({ taskCompletionNotifications: true });
    const { observeJobStatusChange } = createCompletionNotificationTracker();

    await observeJobStatusChange(job({ job_id: 'job-d', status: 'running' }));
    await observeJobStatusChange(job({ job_id: 'job-d', status: 'partial_failure', success_count: 2, failure_count: 1 }));
    expect(constructed).toHaveLength(1);
    expect(constructed[0].title).toBe('Task finished with failures');
    expect(constructed[0].body).toContain('2 succeeded, 1 failed');

    await observeJobStatusChange(job({ job_id: 'job-e', status: 'running' }));
    await observeJobStatusChange(job({ job_id: 'job-e', status: 'upstream_error', error: 'quota exhausted' }));
    expect(constructed).toHaveLength(2);
    expect(constructed[1].title).toBe('Task failed');
  });

  it('exposes permission helpers backed by the Notification API', async () => {
    expect(notificationsSupported()).toBe(true);
    expect(notificationPermission()).toBe('granted');
  });
});
