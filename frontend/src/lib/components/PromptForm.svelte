<script lang="ts">
  import PromptHelperPanel from '$lib/components/PromptHelperPanel.svelte';
  import ImageModelPicker from '$lib/components/ImageModelPicker.svelte';
  import { plainTextInput } from '$lib/actions/plainTextInput';
  import { promptForm } from '$lib/features/workspace/formState.svelte';
  import { t } from '$lib/i18n';
  import type { Snippet } from 'svelte';
  import type { PromptFormState } from '$lib/stores/preview';
  import { RESPONSE_FORMAT_OPTIONS, sanitizeQuantityInput } from '$lib/utils/promptForm';
  import { imageQualities, isImage25, MAX_PROMPT_CHARS, promptLength } from '$lib/utils/imageModels';

  interface Props {
    loading?: boolean;
    optimizing?: boolean;
    optimizerEnabled?: boolean;
    editPlannerEnabled?: boolean;
    editPlanning?: boolean;
    hasEditSource?: boolean;
    editSource?: Snippet;
    onSubmit?: () => void;
    onOpenSize?: () => void;
    onOptimize?: () => void;
    onPlanEdit?: () => void;
    onAppendPromptTag?: (value: string) => void;
  }

  let {
    loading = false,
    optimizing = false,
    optimizerEnabled = false,
    editPlannerEnabled = false,
    editPlanning = false,
    hasEditSource = false,
    editSource,
    onSubmit = () => {},
    onOpenSize = () => {},
    onOptimize = () => {},
    onPlanEdit = () => {},
    onAppendPromptTag = () => {}
  }: Props = $props();

  const promptLen = $derived(promptLength(promptForm.prompt));
  const qualities = $derived(imageQualities(promptForm.model));
  const image25 = $derived(isImage25(promptForm.model));
  const promptOnlyMode = $derived(promptForm.apiPath === '/v1/responses' || promptForm.apiPath === '/v1/chat/completions');
  const parameterControlsDisabled = $derived((promptOnlyMode && !hasEditSource) || loading);
  const modeLabel = $derived(promptForm.apiPath === '/v1/chat/completions' ? $t.promptForm.chatCompletionsMode : $t.promptForm.responsesMode);
  const disabledModeLabel = $derived(
    promptForm.apiPath === '/v1/chat/completions' ? $t.promptForm.disabledForChatCompletions : $t.promptForm.disabledForResponses
  );
  const compressionPlaceholder = $derived(
    promptOnlyMode && !hasEditSource
      ? disabledModeLabel
      : promptForm.outputFormat === 'png'
        ? $t.promptForm.disabledForPng
        : '0-100'
  );
  const optimizeDisabled = $derived(loading || optimizing || !optimizerEnabled || !promptForm.prompt.trim());

  const streamUnavailableReason = $derived(
    Number(promptForm.quantity) > 1
      ? $t.promptForm.streamRequiresSingleImage
      : !hasEditSource && promptForm.apiPath !== '/v1/images/generations'
        ? $t.promptForm.streamUnsupportedPath
        : ''
  );
  const streamDisabled = $derived(loading || Boolean(streamUnavailableReason));

  let qualityResetModel = $state('');

  // Field-scoped normalizers: each effect tracks only the fields it
  // constrains, so typing in the prompt never re-runs them.
  $effect(() => {
    if (!imageQualities(promptForm.model).includes(promptForm.quality)) {
      promptForm.quality = 'auto';
      qualityResetModel = promptForm.model;
    }
  });
  $effect(() => {
    if (
      promptForm.stream &&
      (loading || Number(promptForm.quantity) > 1 || (!hasEditSource && promptForm.apiPath !== '/v1/images/generations'))
    ) {
      promptForm.stream = false;
    }
  });
  $effect(() => {
    if (promptForm.outputFormat === 'png' && promptForm.outputCompression !== '') promptForm.outputCompression = '';
  });
  $effect(() => {
    if (promptForm.background === 'transparent' && promptForm.outputFormat === 'jpeg') {
      promptForm.outputFormat = 'png';
      promptForm.outputCompression = '';
    }
  });

  function handleQuantityInput() {
    promptForm.quantity = sanitizeQuantityInput(promptForm.quantity);
  }

  function handlePromptKeydown(event: KeyboardEvent) {
    if (event.key !== 'Enter' || event.isComposing || event.repeat) return;
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    if (!loading) onSubmit();
  }

  function clampCompression() {
    if (promptForm.outputCompression === '') return;
    promptForm.outputCompression = String(Math.min(Math.max(Number(promptForm.outputCompression) || 0, 0), 100));
  }
</script>

<section class="app-surface p-4 sm:p-5">
  <div class="mb-4 flex items-start justify-between gap-4">
    <div>
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.promptForm.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.promptForm.subtitle}</p>
    </div>
    {#if promptOnlyMode}
      <span class="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-xs font-medium text-cyan-200">{modeLabel}</span>
    {/if}
  </div>

  <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px] lg:items-stretch">
    <div class="min-w-0 flex h-full flex-col">
      <div class="mb-2 flex items-center justify-between gap-3">
        <label for="prompt" class="text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.common.prompt}</label>
        <button
          type="button"
          disabled={optimizeDisabled}
          class="control-focus rounded-lg border border-emerald-500/40 px-3 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-emerald-700 disabled:opacity-60 dark:text-emerald-200 dark:disabled:border-zinc-700 dark:disabled:text-emerald-200"
          title={optimizerEnabled ? $t.promptForm.optimize : $t.promptForm.optimizerUnavailable}
          onclick={onOptimize}
        >
          {optimizing ? $t.promptForm.optimizing : $t.promptForm.optimize}
        </button>
      </div>
      <div class="relative flex min-h-[13rem] flex-1">
        <textarea
          id="prompt"
          name="prompt"
          bind:value={promptForm.prompt}
          aria-invalid={promptLen > MAX_PROMPT_CHARS}
          rows="8"
          autocomplete="off"
          spellcheck="false"
          aria-label={$t.common.prompt}
          placeholder={$t.promptForm.placeholder}
          class="ui-field h-full min-h-[13rem] flex-1 resize-y px-4 py-3 pb-8 leading-6 lg:resize-none"
          use:plainTextInput
          onkeydown={handlePromptKeydown}
        ></textarea>
        <div class="pointer-events-none absolute bottom-3 right-4 text-xs text-stone-500 dark:text-zinc-500">{promptLen}/{MAX_PROMPT_CHARS}</div>
      </div>
    </div>

    <PromptHelperPanel onAppend={onAppendPromptTag} />
  </div>

  <!-- Parameters Section: Grouped for Visual Balance & Clarity -->
  <div class="mt-5 space-y-3">
    <!-- Block 1: Core Generation Parameters (4 columns, balanced 100%) -->
    <div class="app-well rounded-xl border border-stone-200/80 bg-stone-50/50 p-3.5 dark:border-zinc-800/80 dark:bg-zinc-950/40">
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <ImageModelPicker bind:value={promptForm.model} disabled={loading} />

        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.common.size}</span>
          <button
            type="button"
            disabled={parameterControlsDisabled}
            class="control-focus flex h-10 w-full items-center justify-between rounded-lg border border-stone-200 bg-white px-3 text-left font-mono text-sm text-stone-900 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"
            onclick={onOpenSize}
          >
            <span class="truncate">{promptOnlyMode && !hasEditSource ? disabledModeLabel : promptForm.size}</span>
            <span class="ml-1 text-xs text-stone-400 dark:text-zinc-500">⚙</span>
          </button>
        </label>

        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.quality}</span>
          <select bind:value={promptForm.quality} aria-label={$t.promptForm.quality} disabled={parameterControlsDisabled} class="control-focus form-select !bg-white focus:border-emerald-500 dark:!bg-zinc-900">
            {#each qualities as quality}<option value={quality}>{quality}</option>{/each}
          </select>
          {#if qualityResetModel === promptForm.model && promptForm.quality === 'auto'}<p role="status" class="mt-1 text-xs text-stone-500">{$t.promptForm.qualityReset}</p>{/if}
        </label>

        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.quantity}</span>
          <input
            bind:value={promptForm.quantity}
            disabled={parameterControlsDisabled}
            type="text"
            inputmode="numeric"
            pattern="[0-9]*"
            class="control-focus h-10 w-full rounded-lg border border-stone-200 bg-white px-3 text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
            oninput={handleQuantityInput}
          />
        </label>
      </div>
    </div>

    <!-- Block 2: Output Style & Protocol Dual Cards (7:5 split on desktop, balanced and aligned) -->
    <div class="grid gap-3 lg:grid-cols-12">
      <!-- Output Settings (3 columns: Format, Compression, Background) -->
      <div class="app-well rounded-xl border border-stone-200/80 bg-stone-50/50 p-3.5 lg:col-span-7 dark:border-zinc-800/80 dark:bg-zinc-950/40">
        <div class="grid gap-3 sm:grid-cols-3">
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.format}</span>
            <select bind:value={promptForm.outputFormat} disabled={parameterControlsDisabled} class="control-focus form-select !bg-white focus:border-emerald-500 dark:!bg-zinc-900">
              <option value="png">png</option>
              <option value="jpeg">jpeg</option>
              <option value="webp">webp</option>
            </select>
          </label>

          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.compression}</span>
            <input
              bind:value={promptForm.outputCompression}
              disabled={parameterControlsDisabled || promptForm.outputFormat === 'png'}
              type="number"
              min="0"
              max="100"
              placeholder={compressionPlaceholder}
              class="control-focus h-10 w-full rounded-lg border border-stone-200 bg-white px-3 text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
              oninput={clampCompression}
            />
          </label>

          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.background}</span>
            <select bind:value={promptForm.background} disabled={parameterControlsDisabled} class="control-focus form-select !bg-white focus:border-emerald-500 dark:!bg-zinc-900">
              <option value="auto">{$t.promptForm.backgroundAuto}</option>
              <option value="opaque">{$t.promptForm.backgroundOpaque}</option>
              <option value="transparent">{$t.promptForm.backgroundTransparent}</option>
            </select>
            {#if promptForm.background === 'transparent' && promptForm.outputFormat === 'jpeg'}
              <p class="mt-1 text-xs text-amber-600 dark:text-amber-400">{$t.promptForm.backgroundTransparentNote}</p>
            {/if}
          </label>
        </div>
      </div>

      <!-- Protocol Settings (2 columns: API Path, Response Format) -->
      <div class="app-well rounded-xl border border-stone-200/80 bg-stone-50/50 p-3.5 lg:col-span-5 dark:border-zinc-800/80 dark:bg-zinc-950/40">
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.apiPath}</span>
            <select bind:value={promptForm.apiPath} disabled={loading} class="control-focus form-select !bg-white font-mono focus:border-emerald-500 dark:!bg-zinc-900">
              <option value="/v1/images/generations">/v1/images/generations</option>
              <option value="/v1/responses">/v1/responses</option>
              <option value="/v1/chat/completions">/v1/chat/completions</option>
            </select>
          </label>

          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.responseFormat}</span>
            <select value={image25 ? '' : promptForm.responseFormat} onchange={(event) => (promptForm.responseFormat = event.currentTarget.value as PromptFormState['responseFormat'])} disabled={parameterControlsDisabled || image25} class="control-focus form-select !bg-white focus:border-emerald-500 dark:!bg-zinc-900">
              {#each RESPONSE_FORMAT_OPTIONS as responseFormat}
                <option value={responseFormat}>{responseFormat || (image25 ? $t.promptForm.base64Automatic : $t.promptForm.defaultResponseFormat)}</option>
              {/each}
            </select>
          </label>
        </div>
      </div>
    </div>

    <!-- Block 3: Streaming Preview -->
    <div class="app-well rounded-xl border border-stone-200/80 bg-stone-50/50 p-3.5 dark:border-zinc-800/80 dark:bg-zinc-950/40">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <label class="mobile-touch-target flex items-center gap-2 text-xs font-medium text-stone-600 dark:text-zinc-400">
          <input type="checkbox" class="h-3.5 w-3.5 accent-emerald-500" bind:checked={promptForm.stream} disabled={streamDisabled} />
          {$t.promptForm.streamToggle}
        </label>
        {#if promptForm.stream && !streamDisabled}
          <label class="flex items-center gap-2 text-xs font-medium text-stone-600 dark:text-zinc-400">
            {$t.promptForm.streamPartialImages}
            <select
              bind:value={promptForm.partialImages}
              aria-label={$t.promptForm.streamPartialImages}
              class="control-focus form-select !bg-white text-xs focus:border-emerald-500 dark:!bg-zinc-900"
            >
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </label>
        {/if}
      </div>
      {#if streamUnavailableReason}
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{streamUnavailableReason}</p>
      {:else if promptForm.stream}
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{$t.promptForm.streamCostNote}</p>
      {/if}
    </div>
  </div>

  <div class="mt-4">
    {@render editSource?.()}
  </div>

  <div class="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-stone-200/80 pt-4 dark:border-zinc-800/80">
    <div class="flex items-center gap-2 text-xs text-stone-500 dark:text-zinc-400">
      {#if hasEditSource}
        <span class="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 font-medium text-emerald-700 dark:text-emerald-300">
          <span class="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
          {$t.promptForm.edits}
        </span>
      {:else}
        <span class="inline-flex items-center gap-1.5 rounded-full bg-stone-200/60 px-2.5 py-1 font-medium text-stone-600 dark:bg-zinc-800/80 dark:text-zinc-300">
          {$t.promptForm.generate}
        </span>
      {/if}
    </div>

    <div class="flex flex-col items-end gap-1">
      <div class="flex items-center gap-2">
        <button
          type="button"
          disabled={loading || editPlanning || !editPlannerEnabled || !promptForm.prompt.trim()}
          class="ui-button-secondary px-4"
          onclick={onPlanEdit}
        >
          {editPlanning ? $t.promptForm.planningEdit : $t.promptForm.planEdit}
        </button>
        <button type="button" disabled={loading} class="ui-button-primary px-5 font-semibold" onclick={onSubmit}>
          {hasEditSource ? $t.promptForm.edits : $t.promptForm.generate}
        </button>
      </div>
      <p class="text-[11px] text-stone-400 dark:text-zinc-500">{$t.promptForm.submitShortcut}</p>
    </div>
  </div>
</section>
