<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import Overlay from '$lib/components/Overlay.svelte';
  import type { AssistantJobDiagnoseResponse } from '$lib/api/types/assistant';
  import type { GenerateJobStatus } from '$lib/api/types/jobs';
  import JobHistoryList from '$lib/components/JobHistoryList.svelte';
  import RunningJobsList from '$lib/components/RunningJobsList.svelte';
  import { t } from '$lib/i18n';

  type JobsTab = 'running' | 'history';
  type MaybePromise = void | Promise<void>;
  type Props = {
    open?: boolean;
    activeTab?: JobsTab;
    jobs?: GenerateJobStatus[];
    historyJobs?: GenerateJobStatus[];
    historyLoading?: boolean;
    historyLoaded?: boolean;
    historyHasMore?: boolean;
    historyFailedOnly?: boolean;
    selectedIds?: Set<string>;
    onClose?: () => void;
    onTabChange?: (tab: JobsTab) => void;
    onRefresh?: () => MaybePromise;
    onRefreshHistory?: () => MaybePromise;
    onLoadMoreHistory?: () => MaybePromise;
    onHistoryFailedOnlyChange?: (failedOnly: boolean) => MaybePromise;
    onClearHistory?: () => MaybePromise;
    onToggle?: (jobId: string) => void;
    onToggleAll?: () => void;
    onCancelSelected?: () => MaybePromise;
    onUseJob?: (job: GenerateJobStatus) => void;
    onRetryJob?: (job: GenerateJobStatus) => void;
    aiAssistantEnabled?: boolean;
    diagnosingJobId?: string | null;
    diagnoses?: Record<string, AssistantJobDiagnoseResponse>;
    onDiagnoseJob?: (job: GenerateJobStatus) => MaybePromise;
  };

  let {
    open = false,
    activeTab = 'running',
    jobs = [],
    historyJobs = [],
    historyLoading = false,
    historyLoaded = false,
    historyHasMore = false,
    historyFailedOnly = false,
    selectedIds = new Set<string>(),
    onClose = () => {},
    onTabChange = () => {},
    onRefresh = () => {},
    onRefreshHistory = () => {},
    onLoadMoreHistory = () => {},
    onHistoryFailedOnlyChange = () => {},
    onClearHistory = () => {},
    onToggle = () => {},
    onToggleAll = () => {},
    onCancelSelected = () => {},
    onUseJob = () => {},
    onRetryJob = () => {},
    aiAssistantEnabled = false,
    diagnosingJobId = null,
    diagnoses = {},
    onDiagnoseJob = () => {}
  }: Props = $props();

  let internalActiveTab = $state<JobsTab>('running');

  // Remote work changes while you watch it; mark the rows that just moved.
  const lastSeenStatus = new Map<string, string>();
  const settleTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let settledJobIds = $state(new Set<string>());

  function markSettled(jobId: string) {
    const next = new Set(settledJobIds);
    next.add(jobId);
    settledJobIds = next;
    clearTimeout(settleTimers.get(jobId));
    settleTimers.set(
      jobId,
      setTimeout(() => {
        const after = new Set(settledJobIds);
        after.delete(jobId);
        settledJobIds = after;
        settleTimers.delete(jobId);
      }, 620)
    );
  }

  $effect(() => {
    const visible = jobs;
    untrack(() => {
      for (const job of visible) {
        const previous = lastSeenStatus.get(job.job_id);
        if (previous !== undefined && previous !== job.status) markSettled(job.job_id);
        lastSeenStatus.set(job.job_id, job.status);
      }
    });
  });

  onDestroy(() => {
    settleTimers.forEach((timer) => clearTimeout(timer));
    settleTimers.clear();
  });

  $effect(() => {
    if (!open && internalActiveTab !== 'running') internalActiveTab = 'running';
    else if (open && internalActiveTab !== activeTab) internalActiveTab = activeTab;
  });

  function selectTab(tab: JobsTab) {
    internalActiveTab = tab;
    onTabChange(tab);
    if (tab === 'history' && !historyLoaded && !historyLoading) void onRefreshHistory();
  }

  function refreshCurrentTab() {
    if (internalActiveTab === 'history') void onRefreshHistory();
    else void onRefresh();
  }

  function toggleHistoryFailedOnly(event: Event) {
    const failedOnly = (event.currentTarget as HTMLInputElement).checked;
    void onHistoryFailedOnlyChange(failedOnly);
  }

</script>

<Overlay
  {open}
  variant="drawer"
  {onClose}
  closeLabel={$t.jobs.closeLabel}
  labelledBy="jobs-drawer-title"
  z={50}
  panelId="jobs-drawer"
>
      <div class="flex items-center justify-between border-b border-stone-200 p-5 dark:border-zinc-800">
        <div class="min-w-0">
          <h2 id="jobs-drawer-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.jobs.title}</h2>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.jobs.subtitle}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.jobs.closeLabel} onclick={onClose}>x</button>
      </div>

      <div class="flex flex-col gap-3 border-b border-stone-200 p-5 sm:flex-row sm:items-center sm:justify-between dark:border-zinc-800">
        <div class="grid grid-cols-2 rounded-lg border border-stone-200 bg-stone-100 p-1 text-xs font-medium dark:border-zinc-800 dark:bg-zinc-950">
          <button type="button" class={`control-focus rounded-md px-3 py-1.5 ${internalActiveTab === 'running' ? 'bg-white text-stone-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100 dark:shadow-none' : 'text-stone-500 hover:text-stone-900 dark:text-zinc-500 dark:hover:text-zinc-200'}`} onclick={() => selectTab('running')}>
            {$t.jobs.runningTab}
          </button>
          <button type="button" class={`control-focus rounded-md px-3 py-1.5 ${internalActiveTab === 'history' ? 'bg-white text-stone-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100 dark:shadow-none' : 'text-stone-500 hover:text-stone-900 dark:text-zinc-500 dark:hover:text-zinc-200'}`} onclick={() => selectTab('history')}>
            {$t.jobs.historyTab}
          </button>
        </div>
        <div class="flex flex-wrap justify-end gap-3">
          {#if internalActiveTab === 'running'}
            <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!jobs.length} onclick={onToggleAll}>
              {$t.jobs.selectAll}
            </button>
          {/if}
          {#if internalActiveTab === 'history'}
            <label class={`control-focus flex items-center gap-2 rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 dark:border-zinc-700 dark:text-zinc-300 ${historyLoading ? 'cursor-not-allowed opacity-40' : 'cursor-pointer hover:bg-stone-100 dark:hover:bg-zinc-800'}`}>
              <input
                type="checkbox"
                class="h-3.5 w-3.5 accent-red-500"
                checked={historyFailedOnly}
                disabled={historyLoading}
                aria-label={$t.jobs.errorsOnly}
                onchange={toggleHistoryFailedOnly}
              />
              <span>{$t.jobs.errorsOnly}</span>
            </label>
            <button
              type="button"
              class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs font-medium text-red-200 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={historyLoading}
              onclick={onClearHistory}
            >
              {$t.jobs.clearHistory}
            </button>
          {/if}
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={internalActiveTab === 'history' && historyLoading} onclick={refreshCurrentTab}>
            {$t.jobs.refresh}
          </button>
        </div>
      </div>

      {#if internalActiveTab === 'running'}
        <RunningJobsList {jobs} {selectedIds} {settledJobIds} {onToggle} />
      {:else}
        <JobHistoryList
          {historyJobs}
          {historyLoading}
          {historyLoaded}
          {historyHasMore}
          {historyFailedOnly}
          {aiAssistantEnabled}
          {diagnosingJobId}
          {diagnoses}
          {onLoadMoreHistory}
          {onUseJob}
          {onRetryJob}
          {onDiagnoseJob}
        />
      {/if}

      {#if internalActiveTab === 'running'}
        <div class="border-t border-zinc-800 p-5">
          <button type="button" disabled={!selectedIds.size} class="control-focus w-full rounded-xl bg-red-600 px-4 py-3 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40" onclick={onCancelSelected}>
            {$t.jobs.cancelSelected}
          </button>
        </div>
      {/if}
</Overlay>
