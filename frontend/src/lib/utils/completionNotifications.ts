import { get } from 'svelte/store';
import { t } from '$lib/i18n';
import { preferencesStore } from '$lib/stores/preferences';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import type { AgentTurnStatus } from '$lib/api/types/agent';
import { isActiveJobStatus } from '$lib/utils/jobs';

export type NotificationPermissionState = 'unsupported' | 'default' | 'granted' | 'denied';
export type CompletionTarget = { kind: 'job'; jobId: string } | { kind: 'agent'; conversationId: string; turnId: string };
export type AgentCompletion = { turnId: string; conversationId: string; status: AgentTurnStatus; successCount: number; failureCount: number };
export const OPEN_COMPLETION_EVENT = 'gpt-image-panel-open-completion';
const STORAGE_KEY = 'gpt-image-panel-notified-jobs';
const LIMIT = 200;

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window && window.isSecureContext;
}

export function notificationPermission(): NotificationPermissionState {
  return notificationsSupported() ? Notification.permission : 'unsupported';
}

export async function requestNotificationPermission(): Promise<NotificationPermissionState> {
  if (!notificationsSupported()) return 'unsupported';
  try { return await Notification.requestPermission(); } catch { return 'denied'; }
}

function readCompleted(): string[] {
  try {
    const ids = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string').slice(-LIMIT) : [];
  } catch { return []; }
}

function remember(set: Set<string>, id: string) {
  set.add(id);
  if (set.size > LIMIT) set.delete(set.values().next().value!);
}

function canNotify() {
  return get(preferencesStore).taskCompletionNotifications && notificationPermission() === 'granted' &&
    (typeof document === 'undefined' || document.hidden || !document.hasFocus());
}

function show(title: string, body: string, id: string, target: CompletionTarget) {
  try {
    const notification = new Notification(title, { body: body.slice(0, 160), tag: `gpt-image-panel-${id}`, data: target });
    notification.onclick = () => {
      window.focus();
      notification.close();
      window.dispatchEvent(new CustomEvent<CompletionTarget>(OPEN_COMPLETION_EVENT, { detail: target }));
    };
  } catch { /* The page's task status remains available when the browser refuses notifications. */ }
}

export function createCompletionNotificationTracker() {
  const active = new Set<string>();
  const terminal = new Set<string>();

  async function notifyOnce(id: string, showNotification: () => void) {
    const claim = () => {
      const completed = readCompleted();
      if (completed.includes(id)) return;
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...completed, id].slice(-LIMIT))); } catch { /* Session dedupe still applies. */ }
      if (canNotify()) showNotification();
    };
    try {
      if (typeof navigator !== 'undefined' && navigator.locks) {
        // Lock plus a fresh storage read serializes simultaneous terminal SSE
        // events in different tabs. Constructor-time snapshots cannot do this.
        await navigator.locks.request(STORAGE_KEY, claim);
      } else {
        // Older browsers elect the last claimant before displaying. Web Locks
        // provide the stronger guarantee on current secure-context browsers.
        const key = `${STORAGE_KEY}-claim-${id}`;
        const owner = `${id}:${Math.random()}`;
        let stored = false;
        try { localStorage.setItem(key, owner); stored = true; } catch { /* Storage disabled. */ }
        if (stored) {
          await new Promise((resolve) => setTimeout(resolve, 60));
          if (localStorage.getItem(key) !== owner) return;
          localStorage.removeItem(key);
        }
        claim();
      }
    } catch { /* A denied lock/storage API must not disrupt task handling. */ }
  }

  function transition(id: string, running: boolean, showNotification: () => void): Promise<void> {
    if (terminal.has(id)) return Promise.resolve();
    if (running) {
      remember(active, id);
      return Promise.resolve();
    }
    remember(terminal, id);
    if (!active.delete(id)) return Promise.resolve(); // History and first terminal snapshots never notify.
    return notifyOnce(id, showNotification);
  }

  function observeJobStatusChange(job: GenerateJobStatus): Promise<void> {
    if (!job?.job_id || job.agent_turn_id) return Promise.resolve();
    return transition(`job-${job.job_id}`, isActiveJobStatus(job.status), () => {
      const messages = get(t).notifications;
      const title = job.status === 'success' ? messages.jobSucceeded : job.status === 'partial_failure' ? messages.jobPartialFailure : messages.jobFailed;
      const counts = messages.partialFailureCounts(job.success_count ?? job.images?.length ?? 0, job.failure_count ?? 0);
      const body = job.status === 'partial_failure' ? `${counts} — ${job.prompt || ''}` : job.prompt || job.error || '';
      show(title, body, `job-${job.job_id}`, { kind: 'job', jobId: job.job_id });
    });
  }

  function observeAgentTurn(turn: AgentCompletion): Promise<void> {
    if (!turn.turnId) return Promise.resolve();
    return transition(`agent-${turn.turnId}`, turn.status === 'running' || turn.status === 'queued', () => {
      const messages = get(t).notifications;
      const title = turn.status === 'completed' ? messages.agentCompleted : messages.agentFailed;
      show(title, messages.partialFailureCounts(turn.successCount, turn.failureCount), `agent-${turn.turnId}`,
        { kind: 'agent', conversationId: turn.conversationId, turnId: turn.turnId });
    });
  }

  return { observeJobStatusChange, observeAgentTurn };
}

const sharedTracker = createCompletionNotificationTracker();
export const observeJobStatusChange = sharedTracker.observeJobStatusChange;
export const observeAgentTurn = sharedTracker.observeAgentTurn;
