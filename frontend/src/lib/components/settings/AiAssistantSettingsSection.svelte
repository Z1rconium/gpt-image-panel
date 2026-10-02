<script lang="ts">
  import { t } from '$lib/i18n';

  export let enabled = false;
  export let visionModel = '';
  export let agentEnabled = false;
  export let agentModel = '';
  export let agentMaxToolRounds: number | string = 4;
  export let agentSystemPrompt = '';
  export let agentWebSearchEnabled = false;
  export let agentWebSearchSupported = false;
  export let healthChecking = false;
  export let onCheck: () => void | Promise<void> = () => {};
</script>

<section class="border-t border-stone-200 pt-4 dark:border-zinc-800">
  <div class="mb-3 flex items-center justify-between gap-3">
    <div>
      <h3 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.aiAssistant}</h3>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.aiAssistantHint}</p>
    </div>
    <label class="flex items-center gap-2 text-xs font-medium text-stone-700 dark:text-zinc-300">
      <input bind:checked={enabled} type="checkbox" class="control-focus accent-emerald-500" />
      {$t.settings.aiAssistantEnabled}
    </label>
  </div>
  <div class="space-y-4">
    <p class="rounded-md border border-stone-200 bg-stone-50 p-3 text-xs text-stone-600 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">{$t.settings.aiAssistantSharedConfigHint}</p>
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.aiAssistantVisionModel}</span>
      <input bind:value={visionModel} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="gpt-4o-mini" />
    </label>
    <div class="space-y-3 rounded-md border border-stone-200 p-3 dark:border-zinc-800" data-testid="agent-settings">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h4 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.agentMode}</h4>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.agentModeHint}</p>
        </div>
        <label class="flex shrink-0 items-center gap-2 text-xs font-medium text-stone-700 dark:text-zinc-300">
          <input bind:checked={agentEnabled} disabled={!enabled} type="checkbox" class="control-focus accent-emerald-500 disabled:opacity-50" />
          {$t.settings.agentEnabled}
        </label>
      </div>
      {#if !enabled}
        <p class="text-xs text-stone-500 dark:text-zinc-500">{$t.settings.agentRequiresAssistant}</p>
      {/if}
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.agentModel}</span>
        <input bind:value={agentModel} disabled={!enabled} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder={$t.settings.agentModelPlaceholder} />
      </label>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.agentMaxToolRounds}</span>
        <input bind:value={agentMaxToolRounds} disabled={!enabled} type="number" min="1" max="64" inputmode="numeric" class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" />
      </label>
      <label class="flex items-center gap-2 text-xs">
        <input type="checkbox" class="control-focus accent-emerald-500" bind:checked={agentWebSearchSupported} disabled={!enabled} />
        {$t.settings.agentSearchSupported}
      </label>
      <label class="flex items-center gap-2 text-xs">
        <input type="checkbox" class="control-focus accent-emerald-500" bind:checked={agentWebSearchEnabled} disabled={!enabled || !agentWebSearchSupported} />
        {$t.settings.agentSearchEnabled}
      </label>
      <p class="text-xs text-stone-500 dark:text-zinc-400">{$t.settings.agentSearchHint}</p>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.agentSystemPrompt}</span>
        <textarea bind:value={agentSystemPrompt} disabled={!enabled} rows="3" maxlength="4000" class="control-focus w-full resize-y rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 text-sm text-stone-900 focus:border-emerald-500 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder={$t.settings.agentSystemPromptPlaceholder}></textarea>
        <span class="mt-1 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.agentSystemPromptHint}</span>
      </label>
    </div>
    <button type="button" disabled={healthChecking} class="control-focus w-full rounded-md border border-emerald-500/40 px-3 py-2.5 text-sm font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-200" on:click={onCheck}>
      {healthChecking ? $t.settings.aiAssistantHealthChecking : $t.settings.aiAssistantHealthCheck}
    </button>
  </div>
</section>
