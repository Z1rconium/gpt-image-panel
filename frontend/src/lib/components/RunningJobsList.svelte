<script lang="ts">
  import type { GenerateJobStatus } from '$lib/api/types/jobs';
  import { t } from '$lib/i18n';
  import { operationLabel, stageLabel, statusLabel } from '$lib/utils/format';
  import { measureItem, observeViewport } from '$lib/actions/virtualList';

  type Props = {
    jobs?: GenerateJobStatus[];
    selectedIds?: Set<string>;
    settledJobIds?: Set<string>;
    onToggle?: (jobId: string) => void;
  };

  const ESTIMATED_ITEM_HEIGHT = 120;
  const ITEM_GAP = 12;
  const OVERSCAN_PX = 720;

  let { jobs = [], selectedIds = new Set<string>(), settledJobIds = new Set<string>(), onToggle = () => {} }: Props = $props();

  let scrollTop = $state(0);
  let viewportHeight = $state(0);
  let measuredHeights = $state<Record<string, number>>({});

  const layout = $derived.by(() => {
    const offsets = new Array<number>(jobs.length + 1);
    offsets[0] = 0;
    for (let index = 0; index < jobs.length; index += 1) {
      const height = measuredHeights[jobs[index].job_id] || ESTIMATED_ITEM_HEIGHT;
      offsets[index + 1] = offsets[index] + height + (index < jobs.length - 1 ? ITEM_GAP : 0);
    }
    return { offsets, totalHeight: offsets[jobs.length] || 0 };
  });
  const renderWindow = $derived.by(() => {
    if (!jobs.length) return { start: 0, end: 0 };
    const rangeStart = Math.max(0, scrollTop - OVERSCAN_PX);
    const rangeEnd = scrollTop + Math.max(viewportHeight, ESTIMATED_ITEM_HEIGHT) + OVERSCAN_PX;
    let start = 0;
    while (start < jobs.length - 1 && layout.offsets[start + 1] < rangeStart) start += 1;
    let end = start + 1;
    while (end < jobs.length && layout.offsets[end] < rangeEnd) end += 1;
    return { start, end: Math.min(jobs.length, end + 1) };
  });
  const renderedJobs = $derived(jobs.slice(renderWindow.start, renderWindow.end));
  const topSpacerHeight = $derived(layout.offsets[renderWindow.start] || 0);
  const renderedHeight = $derived(
    (layout.offsets[renderWindow.end] || 0) - topSpacerHeight - (renderWindow.end < jobs.length ? ITEM_GAP : 0)
  );
  const bottomSpacerHeight = $derived(Math.max(0, layout.totalHeight - topSpacerHeight - renderedHeight));

  function handleViewportResize(height: number) {
    viewportHeight = height;
  }

  function handleItemMeasure(jobId: string, height: number) {
    if (measuredHeights[jobId] !== height) {
      measuredHeights = { ...measuredHeights, [jobId]: height };
    }
  }

  function handleScroll(event: Event) {
    scrollTop = (event.currentTarget as HTMLDivElement).scrollTop;
  }

  function statusClass(job: GenerateJobStatus) {
    if (job.status === 'success') return 'text-emerald-300';
    if (job.status === 'partial_failure') return 'text-amber-700 dark:text-amber-300';
    if (job.status === 'error' || job.status === 'upstream_error') return 'text-red-300';
    if (job.status === 'cancelled') return 'text-stone-500 dark:text-zinc-400';
    if (job.status === 'interrupted') return 'text-amber-300';
    if (job.status === 'running') return 'text-cyan-300';
    return 'text-amber-300';
  }
</script>

<div class="mobile-drawer-scroll min-h-0 flex-1 overflow-y-auto p-5" onscroll={handleScroll} use:observeViewport={handleViewportResize}>
  {#if jobs.length === 0}
    <div class="rounded-xl border border-dashed border-stone-300 bg-stone-100/80 px-4 py-10 text-center dark:border-zinc-800 dark:bg-zinc-950/35">
      <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{$t.jobs.noRunning}</p>
      <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{$t.jobs.noRunningHint}</p>
    </div>
  {:else}
    <div style={`height: ${topSpacerHeight}px`} aria-hidden="true"></div>
    <div class="space-y-3">
      {#each renderedJobs as job, renderedIndex (job.job_id)}
        <article
          class="flex gap-3 rounded-xl border border-stone-200 bg-stone-50/80 p-4 dark:border-zinc-800 dark:bg-zinc-950/45"
          class:job-settled={settledJobIds.has(job.job_id)}
          use:measureItem={{ id: job.job_id, onMeasure: handleItemMeasure }}
          aria-posinset={renderWindow.start + renderedIndex + 1}
          aria-setsize={jobs.length}
        >
          <input
            type="checkbox"
            class="control-focus mt-1 accent-emerald-500"
            checked={selectedIds.has(job.job_id)}
            aria-label={`${$t.jobs.selectAll}: ${job.prompt || $t.common.untitledJob}`}
            onchange={() => onToggle(job.job_id)}
          />
          <div class="min-w-0 flex-1">
            <div class="flex items-center justify-between gap-3">
              <span class="rounded-md border border-stone-300 px-2 py-0.5 text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-400">{operationLabel(job.operation, $t.operations)}</span>
              <span class={`text-xs font-medium ${statusClass(job)}`}>{statusLabel(job.status, $t.statuses)}</span>
            </div>
            <p class="mt-2 truncate text-sm text-stone-800 dark:text-zinc-200">{job.prompt || $t.common.untitledJob}</p>
            <p class="mt-1 truncate text-xs text-stone-500 dark:text-zinc-500">{stageLabel(job, $t.stages)}</p>
          </div>
        </article>
      {/each}
    </div>
    <div style={`height: ${bottomSpacerHeight}px`} aria-hidden="true"></div>
  {/if}
</div>
