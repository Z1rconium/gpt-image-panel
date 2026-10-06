<script lang="ts">
  import { t } from '$lib/i18n';
  import { apiFetch } from '$lib/api/client';
  import { copyText } from '$lib/utils/format';
  import type { JobDiagnosticsResponse, JobUnitDiagnostics } from '$lib/api/types/jobs';

  type DiagnosticStage = {
    phase?: string;
    at?: string;
    http?: { method?: string; url?: string; status?: number };
    url?: string;
    status?: number;
    message?: string;
    error?: string;
    mapping_path?: string;
    snapshot?: unknown;
    extra?: Record<string, unknown>;
    omitted?: string[];
    truncated?: boolean;
  };

  export let jobId: string;
  export let expanded = false;

  let loading = false;
  let loaded = false;
  let errorMessage = '';
  let response: JobDiagnosticsResponse | null = null;
  let copied = false;

  async function load() {
    loading = true;
    errorMessage = '';
    try {
      response = await apiFetch<JobDiagnosticsResponse>(
        `/api/generate/${encodeURIComponent(jobId)}/diagnostics`,
        {},
        'loading job diagnostics'
      );
      loaded = true;
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : String(error);
    } finally {
      loading = false;
    }
  }

  $: if (expanded && !loaded && !loading) void load();

  function stagesOf(unit: JobUnitDiagnostics): DiagnosticStage[] {
    const stages = (unit.diagnostics || {}).stages;
    return Array.isArray(stages) ? (stages as DiagnosticStage[]) : [];
  }

  function codeOf(unit: JobUnitDiagnostics): string {
    return String((unit.diagnostics || {}).code || '');
  }

  function httpLine(stage: DiagnosticStage): string {
    const method = stage.http?.method || '';
    const url = stage.http?.url || stage.url || '';
    const status = stage.http?.status ?? stage.status;
    return `${method}${url ? ` ${url}` : ''}${status != null ? ` -> ${status}` : ''}`.trim();
  }

  function diagnosticsText(): string {
    if (!response) return '';
    const lines: string[] = [`job ${response.job_id}`];
    for (const unit of response.units) {
      lines.push(
        `unit #${unit.unit_index + 1} ${unit.status}${unit.stage ? ` (${unit.stage})` : ''} attempts=${unit.attempts} recoveries=${unit.recovery_count}`
      );
      if (unit.error) lines.push(`  error: ${unit.error}`);
      const code = codeOf(unit);
      if (code) lines.push(`  code: ${code}`);
      if (unit.remote) {
        lines.push(
          `  remote: phase=${unit.remote.phase} task=${unit.remote.task_id || '-'} deadline=${unit.remote.deadline_at || '-'} urls(status=${unit.remote.has_status_url}, result=${unit.remote.has_result_url}, cancel=${unit.remote.has_cancel_url})`
        );
      }
      for (const stage of stagesOf(unit)) {
        const http = httpLine(stage);
        lines.push(`  [${stage.phase || 'stage'}] ${http}${stage.message ? ` ${stage.message}` : ''}`);
        if (stage.mapping_path) lines.push(`    mapping: ${stage.mapping_path}`);
        if (stage.error) lines.push(`    error: ${stage.error}`);
        if (stage.snapshot !== undefined) {
          try {
            lines.push(`    snapshot: ${JSON.stringify(stage.snapshot)}`);
          } catch {
            lines.push('    snapshot: [unserializable]');
          }
        }
      }
    }
    return lines.join('\n');
  }

  async function copyDiagnostics() {
    await copyText(diagnosticsText());
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }
</script>

{#if expanded}
  <div class="mt-3 rounded-lg border border-stone-300 bg-stone-100/70 px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-950/40">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="font-semibold text-stone-700 dark:text-zinc-200">{$t.jobs.diagnostics}</span>
      <span class="flex gap-2">
        {#if response}<button type="button" class="control-focus rounded-sm border border-stone-300 px-2 py-1 text-[11px] text-stone-600 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={copyDiagnostics}>{copied ? $t.jobs.diagnosticsCopied : $t.jobs.diagnosticsCopy}</button>{/if}
        <button type="button" class="control-focus rounded-sm border border-stone-300 px-2 py-1 text-[11px] text-stone-600 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={() => { loaded = false; void load(); }}>{$t.jobs.diagnosticsRefresh}</button>
      </span>
    </div>
    {#if loading}
      <p class="mt-2 text-stone-500 dark:text-zinc-500">{$t.jobs.diagnosticsLoading}</p>
    {:else if errorMessage}
      <p role="alert" class="mt-2 text-amber-700 dark:text-amber-300">{errorMessage}</p>
    {:else if !response || response.units.length === 0}
      <p class="mt-2 text-stone-500 dark:text-zinc-500">{$t.jobs.diagnosticsEmpty}</p>
    {:else}
      <ul class="mt-2 space-y-3">
        {#each response.units as unit (unit.unit_id)}
          <li class="rounded-md border border-stone-200 p-2 dark:border-zinc-800">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-medium text-stone-800 dark:text-zinc-200">{$t.jobs.diagnosticsUnit(unit.unit_index + 1)}</span>
              <span class="text-stone-500 dark:text-zinc-500">{unit.status}{unit.stage ? ` · ${unit.stage}` : ''}</span>
              {#if codeOf(unit)}<span class="rounded-sm border border-amber-400/40 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-800 dark:text-amber-200">{codeOf(unit)}</span>{/if}
              {#if unit.recovery_count > 0}<span class="text-stone-500 dark:text-zinc-500">{$t.jobs.diagnosticsRecovery(unit.recovery_count)}</span>{/if}
            </div>
            {#if unit.error}<p class="mt-1 break-words text-amber-700 dark:text-amber-300">{unit.error}</p>{/if}
            {#if unit.remote}
              <dl class="mt-1 grid grid-cols-1 gap-0.5 font-mono text-[11px] text-stone-500 dark:text-zinc-500">
                <div>{$t.jobs.diagnosticsPhase}: {unit.remote.phase}</div>
                {#if unit.remote.task_id}<div>{$t.jobs.diagnosticsTaskId}: {unit.remote.task_id}</div>{/if}
                {#if unit.remote.deadline_at}<div>{$t.jobs.diagnosticsDeadline}: {unit.remote.deadline_at}</div>{/if}
                <div>{$t.jobs.diagnosticsUrls}: status={String(unit.remote.has_status_url)} result={String(unit.remote.has_result_url)} cancel={String(unit.remote.has_cancel_url)}</div>
              </dl>
            {/if}
            {#if stagesOf(unit).length}
              <ul class="mt-2 space-y-2">
                {#each stagesOf(unit) as stage, stageIndex (stageIndex)}
                  <li class="rounded-sm border border-stone-200 bg-stone-50/80 p-2 dark:border-zinc-800 dark:bg-zinc-900/40">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="font-mono text-[11px] text-stone-700 dark:text-zinc-300">[{stage.phase || 'stage'}]</span>
                      <span class="font-mono text-[11px] text-stone-500 dark:text-zinc-500">{httpLine(stage)}</span>
                    </div>
                    {#if stage.message}<p class="mt-1 break-words text-stone-600 dark:text-zinc-400">{stage.message}</p>{/if}
                    {#if stage.mapping_path}<p class="mt-1 font-mono text-[11px] text-stone-500 dark:text-zinc-500">{$t.jobs.diagnosticsMapping}: {stage.mapping_path}</p>{/if}
                    {#if stage.error}<p class="mt-1 break-words text-amber-700 dark:text-amber-300">{stage.error}</p>{/if}
                    {#if stage.omitted?.length}<p class="mt-1 text-[11px] text-stone-400 dark:text-zinc-600">{$t.jobs.diagnosticsOmitted(stage.omitted.length)}</p>{/if}
                    {#if stage.snapshot !== undefined}
                      <details class="mt-1">
                        <summary class="cursor-pointer text-[11px] text-stone-500 dark:text-zinc-500">{$t.jobs.diagnosticsSnapshot}</summary>
                        <pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-stone-600 dark:text-zinc-400">{JSON.stringify(stage.snapshot, null, 2)}</pre>
                      </details>
                    {/if}
                  </li>
                {/each}
              </ul>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </div>
{/if}
