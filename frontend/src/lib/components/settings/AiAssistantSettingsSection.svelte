<script lang="ts">
  import { t } from '$lib/i18n';

  export let enabled = false;
  export let apiUrl = '';
  export let model = '';
  export let visionModel = '';
  export let timeoutSeconds: number | string = 60;
  export let apiKey = '';
  export let agentEnabled = false;
  export let agentModel = '';
  export let agentMaxToolRounds: number | string = 4;
  export let agentSystemPrompt = '';
  export let agentWebSearchEnabled = false;
  export let agentWebSearchSupported = false;
  export let healthChecking = false;
  export let onNormalizeTimeout: () => void = () => {};
  export let onOpenSystemPrompt: () => void | Promise<void> = () => {};
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
    <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerApiUrl}</span>
      <input
        bind:value={apiUrl}
        disabled={!enabled}
        class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600"
        placeholder="https://api.openai.com/v1/chat/completions"
      />
    </label>

    <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerModel}</span>
      <input
        bind:value={model}
        disabled={!enabled}
        class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600"
        placeholder="gpt-4o-mini"
      />
    </label>

    <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.aiAssistantVisionModel}</span>
      <input
        bind:value={visionModel}
        disabled={!enabled}
        class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600"
        placeholder="gpt-4o-mini"
      />
    </label>

    <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerTimeout}</span>
      <input
        bind:value={timeoutSeconds}
        disabled={!enabled}
        type="number"
        min="1"
        step="1"
        inputmode="numeric"
        class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600"
        on:blur={onNormalizeTimeout}
      />
    </label>

    <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerApiKey}</span>
      <input
        bind:value={apiKey}
        disabled={!enabled}
        type="text"
        class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600"
      />
      <span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.apiKeyHint}</span>
    </label>

    <button
      type="button"
      disabled={!enabled}
      class="control-focus w-full rounded-md border border-stone-300 px-3 py-2.5 text-sm font-semibold text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600"
      on:click={onOpenSystemPrompt}
    >
      {$t.settings.editSystemPrompt}
    </button>

    <div class="space-y-3 rounded-md border border-stone-200 p-3 dark:border-zinc-800" data-testid="agent-settings">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h4 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.agentMode}</h4>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.agentModeHint}</p>
        </div>
        <label class="flex shrink-0 items-center gap-2 text-xs font-medium text-stone-700 dark:text-zinc-300 {enabled ? '' : 'cursor-not-allowed opacity-75'}">
          <input bind:checked={agentEnabled} disabled={!enabled} type="checkbox" class="control-focus accent-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" />
          {$t.settings.agentEnabled}
        </label>
      </div>
      {#if !enabled}
        <p class="text-xs text-stone-500 dark:text-zinc-500">{$t.settings.agentRequiresAssistant}</p>
      {/if}
      <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.agentModel}</span>
        <input bind:value={agentModel} disabled={!enabled} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600" placeholder={$t.settings.agentModelPlaceholder} />
      </label>
      <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.agentMaxToolRounds}</span>
        <input bind:value={agentMaxToolRounds} disabled={!enabled} type="number" min="1" max="64" inputmode="numeric" class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600" />
      </label>
      <label class="flex items-center gap-2 text-xs {enabled ? '' : 'cursor-not-allowed opacity-75'}">
        <input type="checkbox" class="control-focus accent-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" bind:checked={agentWebSearchSupported} disabled={!enabled} />
        {$t.settings.agentSearchSupported}
      </label>
      <label class="flex items-center gap-2 text-xs {enabled ? '' : 'cursor-not-allowed opacity-75'}">
        <input type="checkbox" class="control-focus accent-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" bind:checked={agentWebSearchEnabled} disabled={!enabled || !agentWebSearchSupported} />
        {$t.settings.agentSearchEnabled}
      </label>
      <p class="text-xs text-stone-500 dark:text-zinc-400">{$t.settings.agentSearchHint}</p>
      <label class="block {enabled ? '' : 'cursor-not-allowed opacity-75'}">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.agentSystemPrompt}</span>
        <textarea bind:value={agentSystemPrompt} disabled={!enabled} rows="3" maxlength="4000" class="control-focus w-full resize-y rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:border-stone-200 disabled:bg-stone-100 disabled:text-stone-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:disabled:border-zinc-800 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-600" placeholder={$t.settings.agentSystemPromptPlaceholder}></textarea>
        <span class="mt-1 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.agentSystemPromptHint}</span>
      </label>
    </div>

    <button
      type="button"
      disabled={!enabled || healthChecking}
      class="control-focus w-full rounded-md border px-3 py-2.5 text-sm font-semibold transition-colors {enabled ? 'border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200' : 'cursor-not-allowed border-stone-300 bg-stone-100 text-stone-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-600'} disabled:cursor-not-allowed disabled:opacity-50"
      on:click={onCheck}
    >
      {healthChecking ? $t.settings.aiAssistantHealthChecking : $t.settings.aiAssistantHealthCheck}
    </button>
  </div>
</section>
