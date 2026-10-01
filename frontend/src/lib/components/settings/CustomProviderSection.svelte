<script lang="ts">
  import { onDestroy } from 'svelte';
  import { t } from '$lib/i18n';
  import type { ProviderKind } from '$lib/api/types/common';
  import type { PresetImportIssue, ProviderMappingExtraction } from '$lib/api/types/settings';
  import { parseProviderConfigText } from '$lib/features/settings/draft';
  import { settingsStore } from '$lib/stores/settings';
  import { copyText } from '$lib/utils/format';

  export let providerKind: ProviderKind = 'openai';
  export let providerConfigText = '';
  export let supportsMask = true;

  // A neutral skeleton showing the shape; the docs describe every field.
  const MAPPING_PLACEHOLDER = JSON.stringify(
    {
      version: 1,
      auth: { header: 'Authorization', scheme: 'Bearer' },
      submit: { path: '/{{model}}', body: { prompt: '{{prompt}}' } },
      poll: { url_path: '$.status_url', status_path: '$.status', done: ['COMPLETED'], failed: ['FAILED'] },
      result: { url_path: '$.result_url', images_path: '$.images[*].url', image_kind: 'url' }
    },
    null,
    2
  );

  const VALIDATION_DEBOUNCE_MS = 600;

  let promptLoading = false;
  let promptCopied = false;
  let validating = false;
  let validationErrors: PresetImportIssue[] = [];
  let validationTimer: ReturnType<typeof setTimeout> | null = null;
  let samplesOpen = false;
  let sampleSubmit = '';
  let samplePoll = '';
  let sampleResult = '';
  let sampleError = '';
  let extraction: ProviderMappingExtraction | null = null;

  $: parsed = parseProviderConfigText(providerConfigText);
  $: configError =
    providerKind !== 'async_json'
      ? ''
      : !providerConfigText.trim()
        ? $t.settings.providerConfigRequired
        : parsed.ok
          ? ''
          : $t.settings.providerConfigInvalidJson;

  function selectKind(event: Event) {
    providerKind = (event.currentTarget as HTMLSelectElement).value as ProviderKind;
    if (providerKind === 'async_json') supportsMask = false;
  }

  async function copyMappingPrompt() {
    promptLoading = true;
    try {
      const response = await settingsStore.loadProviderMappingPrompt();
      await copyText(response.prompt);
      promptCopied = true;
      setTimeout(() => (promptCopied = false), 2000);
    } finally {
      promptLoading = false;
    }
  }

  async function runValidation(config: unknown) {
    validating = true;
    try {
      const response = await settingsStore.validateProviderMapping({ provider_config: config });
      validationErrors = response.valid ? [] : response.errors;
    } catch {
      validationErrors = [];
    } finally {
      validating = false;
    }
  }

  function scheduleValidation() {
    if (validationTimer) clearTimeout(validationTimer);
    validationErrors = [];
    extraction = null;
    if (providerKind !== 'async_json' || !parsed.ok || !parsed.value) return;
    const config = parsed.value;
    validationTimer = setTimeout(() => {
      void runValidation(config);
    }, VALIDATION_DEBOUNCE_MS);
  }

  function parseSample(text: string): unknown {
    return text.trim() ? JSON.parse(text) : null;
  }

  async function testExtraction() {
    sampleError = '';
    extraction = null;
    if (!parsed.ok || !parsed.value) {
      sampleError = $t.settings.providerConfigInvalidJson;
      return;
    }
    let submit: unknown;
    let poll: unknown;
    let result: unknown;
    try {
      submit = parseSample(sampleSubmit);
      poll = parseSample(samplePoll);
      result = parseSample(sampleResult);
    } catch {
      sampleError = $t.settings.mappingSampleInvalidJson;
      return;
    }
    const response = await settingsStore.validateProviderMapping({
      provider_config: parsed.value,
      sample_submit: submit,
      sample_poll: poll,
      sample_result: result
    });
    if (!response.valid) {
      validationErrors = response.errors;
      return;
    }
    extraction = response.extraction || null;
  }

  function fieldLabel(result: { found: boolean; value?: string | null; detail?: string | null }): string {
    if (result.found && result.value) return result.value;
    return result.detail || $t.settings.mappingFieldMissing;
  }

  $: if (providerKind === 'async_json') scheduleValidation();
  else {
    validationErrors = [];
    extraction = null;
  }

  onDestroy(() => {
    if (validationTimer) clearTimeout(validationTimer);
  });
</script>

<div class="space-y-3">
  <label class="block">
    <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.providerKind}</span>
    <select value={providerKind} on:change={selectKind} class="control-focus form-select border-stone-300 bg-stone-50 text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
      <option value="openai">{$t.settings.providerOpenAI}</option>
      <option value="async_json">{$t.settings.providerAsync}</option>
    </select>
  </label>

  {#if providerKind === 'async_json'}
    <div class="space-y-2">
      <p class="text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.settings.providerAsyncHint}</p>
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={promptLoading}
          class="control-focus rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          on:click={copyMappingPrompt}
        >
          {promptLoading ? $t.settings.mappingPromptLoading : promptCopied ? $t.settings.mappingPromptCopied : $t.settings.mappingPromptCopy}
        </button>
        {#if validating}<span class="text-xs text-stone-400 dark:text-zinc-500">{$t.settings.mappingValidating}</span>{/if}
      </div>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.providerConfig}</span>
        <textarea bind:value={providerConfigText} rows="14" spellcheck="false" placeholder={MAPPING_PLACEHOLDER} aria-invalid={configError ? 'true' : 'false'} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-xs text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"></textarea>
      </label>
      {#if configError}<p role="alert" class="text-xs text-amber-600">{configError}</p>{/if}
      {#if validationErrors.length}
        <ul role="alert" aria-label={$t.settings.mappingValidationTitle} class="space-y-1 rounded-lg border border-amber-400/40 bg-amber-500/10 px-3 py-2 font-mono text-[11px] text-amber-800 dark:text-amber-200">
          {#each validationErrors as issue}
            <li>{issue.path}: {issue.message}</li>
          {/each}
        </ul>
      {/if}

      <details class="rounded-lg border border-stone-200 p-3 dark:border-zinc-800" bind:open={samplesOpen}>
        <summary class="cursor-pointer text-xs font-medium text-stone-700 dark:text-zinc-300">{$t.settings.mappingSampleTitle}</summary>
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.mappingSampleHint}</p>
        <div class="mt-2 space-y-2">
          <label class="block">
            <span class="mb-1 block text-xs text-stone-500 dark:text-zinc-400">{$t.settings.mappingSampleSubmit}</span>
            <textarea bind:value={sampleSubmit} rows="4" spellcheck="false" class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2 font-mono text-xs text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"></textarea>
          </label>
          <label class="block">
            <span class="mb-1 block text-xs text-stone-500 dark:text-zinc-400">{$t.settings.mappingSamplePoll}</span>
            <textarea bind:value={samplePoll} rows="4" spellcheck="false" class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2 font-mono text-xs text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"></textarea>
          </label>
          <label class="block">
            <span class="mb-1 block text-xs text-stone-500 dark:text-zinc-400">{$t.settings.mappingSampleResult}</span>
            <textarea bind:value={sampleResult} rows="4" spellcheck="false" class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2 font-mono text-xs text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"></textarea>
          </label>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={testExtraction}>
            {$t.settings.mappingSampleVerify}
          </button>
          {#if sampleError}<p role="alert" class="text-xs text-amber-600">{sampleError}</p>{/if}
          {#if extraction}
            <dl class="grid gap-1.5 text-xs">
              <div class="flex gap-2"><dt class="w-24 shrink-0 text-stone-500 dark:text-zinc-500">{$t.settings.mappingTaskId}</dt><dd class={extraction.task_id.found ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300'}>{fieldLabel(extraction.task_id)}</dd></div>
              <div class="flex gap-2"><dt class="w-24 shrink-0 text-stone-500 dark:text-zinc-500">{$t.settings.mappingStatusUrl}</dt><dd class={extraction.status_url.found ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300'}>{fieldLabel(extraction.status_url)}</dd></div>
              <div class="flex gap-2"><dt class="w-24 shrink-0 text-stone-500 dark:text-zinc-500">{$t.settings.mappingResultUrl}</dt><dd class={extraction.result_url.found ? 'text-emerald-700 dark:text-emerald-300' : 'text-stone-500 dark:text-zinc-500'}>{fieldLabel(extraction.result_url)}</dd></div>
              <div class="flex gap-2"><dt class="w-24 shrink-0 text-stone-500 dark:text-zinc-500">{$t.settings.mappingStatusValue}</dt><dd class={extraction.status_value.found ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300'}>{fieldLabel(extraction.status_value)}</dd></div>
              <div class="flex gap-2"><dt class="w-24 shrink-0 text-stone-500 dark:text-zinc-500">{$t.settings.mappingImages}</dt><dd>{extraction.image_count == null ? $t.settings.mappingImagesUnknown : $t.settings.mappingImagesFound(extraction.image_count, extraction.image_kind || '')}</dd></div>
            </dl>
            {#if extraction.image_samples.length}
              <ul class="space-y-1 font-mono text-[11px] text-stone-500 dark:text-zinc-500">
                {#each extraction.image_samples as sample}<li class="truncate">{sample}</li>{/each}
              </ul>
            {/if}
            {#if extraction.notes.length}
              <ul class="space-y-1 text-xs text-amber-700 dark:text-amber-300">
                {#each extraction.notes as note}<li>{note}</li>{/each}
              </ul>
            {/if}
          {/if}
        </div>
      </details>
    </div>
  {/if}
</div>
