<script lang="ts">
  import { tick } from 'svelte';
  import type { AgentImageParams, AgentImageRef } from '$lib/api/types/agent';
  import type { GalleryEntry } from '$lib/api/types/gallery';
  import { t } from '$lib/i18n';
  import { settingsStore } from '$lib/stores/settings';
  import { AGENT_MAX_ATTACHMENTS, type AgentSendInput } from '$lib/stores/agent';
  import { applyMention, filterMentionOptions, findMentionQuery } from '$lib/utils/agentRefs';
  import { thumbnailUrl } from '$lib/utils/format';
  import AgentAttachDialog from './AgentAttachDialog.svelte';

  let {
    imageRefs,
    galleryImages,
    gallerySelectedIds,
    turnActive = false,
    sending = false,
    cancelling = false,
    error = null,
    onSend,
    onStop,
    onDismissError
  }: {
    imageRefs: AgentImageRef[];
    galleryImages: GalleryEntry[];
    gallerySelectedIds: string[];
    turnActive?: boolean;
    sending?: boolean;
    cancelling?: boolean;
    error?: string | null;
    onSend: (input: AgentSendInput) => Promise<boolean>;
    onStop: () => void;
    onDismissError: () => void;
  } = $props();

  const PARAMS_KEY = 'agent.imageParams.v1';
  const SIZE_OPTIONS = ['auto', '1024x1024', '1536x1024', '1024x1536'];
  const QUALITY_OPTIONS: AgentImageParams['quality'][] = ['auto', 'low', 'medium', 'high'];
  const FORMAT_OPTIONS: AgentImageParams['output_format'][] = ['png', 'jpeg', 'webp'];
  const MAX_MENTION_OPTIONS = 8;

  function loadParams(): AgentImageParams {
    const fallback: AgentImageParams = { size: 'auto', quality: 'auto', output_format: 'png' };
    try {
      const raw = JSON.parse(localStorage.getItem(PARAMS_KEY) || 'null');
      if (!raw || typeof raw !== 'object') return fallback;
      return {
        size: SIZE_OPTIONS.includes(raw.size) ? raw.size : fallback.size,
        quality: QUALITY_OPTIONS.includes(raw.quality) ? raw.quality : fallback.quality,
        output_format: FORMAT_OPTIONS.includes(raw.output_format) ? raw.output_format : fallback.output_format
      };
    } catch {
      return fallback;
    }
  }

  let text = $state('');
  let caret = $state(0);
  let textarea: HTMLTextAreaElement | undefined = $state();
  let attachments = $state<GalleryEntry[]>([]);
  let attachOpen = $state(false);
  let params = $state<AgentImageParams>(loadParams());
  let activeIndex = $state(0);
  let dismissedAt = $state<number | null>(null);

  const mention = $derived(findMentionQuery(text, caret));
  const options = $derived(mention ? filterMentionOptions(imageRefs, mention.query).slice(0, MAX_MENTION_OPTIONS) : []);
  const listOpen = $derived(Boolean(mention) && options.length > 0 && dismissedAt !== mention?.start);
  const searchEnabled = $derived(Boolean($settingsStore.settings?.ai_assistant.agent_web_search_enabled));
  const searchUnavailable = $derived(searchEnabled && (
    !$settingsStore.settings?.ai_assistant.agent_web_search_supported ||
    $settingsStore.settings?.ai_assistant.api_path !== '/v1/responses'
  ));
  const canSend = $derived(text.trim().length > 0 && !turnActive && !sending && !searchUnavailable);
  const activeOptionId = $derived(listOpen ? `agent-mention-option-${activeIndex}` : undefined);

  $effect(() => {
    // Keep the highlighted option inside the shrinking or growing list.
    if (activeIndex >= options.length) activeIndex = Math.max(0, options.length - 1);
  });

  function saveParams() {
    try {
      localStorage.setItem(PARAMS_KEY, JSON.stringify(params));
    } catch {
      /* storage may be disabled */
    }
  }

  function syncCaret() {
    caret = textarea?.selectionStart ?? text.length;
  }

  function optionLabel(ref: AgentImageRef) {
    return ref.role === 'output'
      ? $t.agent.imageLabel(ref.path_round_no ?? ref.round_no, ref.image_index)
      : $t.agent.attachmentLabel(ref.path_round_no ?? ref.round_no, ref.image_index);
  }

  async function chooseOption(ref: AgentImageRef) {
    if (!mention) return;
    const next = applyMention(text, mention, ref.ref_label);
    text = next.text;
    activeIndex = 0;
    await tick();
    textarea?.focus();
    textarea?.setSelectionRange(next.caret, next.caret);
    caret = next.caret;
  }

  async function submit() {
    if (!canSend) return;
    const input: AgentSendInput = {
      text,
      attachmentIds: attachments.map((entry) => entry.id),
      imageParams: { ...params }
    };
    const ok = await onSend(input);
    if (ok) {
      text = '';
      attachments = [];
      caret = 0;
    }
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.isComposing) return;
    if (listOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        activeIndex = (activeIndex + 1) % options.length;
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        activeIndex = (activeIndex - 1 + options.length) % options.length;
        return;
      }
      if ((event.key === 'Enter' && !(event.ctrlKey || event.metaKey)) || event.key === 'Tab') {
        event.preventDefault();
        void chooseOption(options[activeIndex]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        dismissedAt = mention?.start ?? null;
        return;
      }
    }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.repeat) {
      event.preventDefault();
      void submit();
    }
  }

  function confirmAttachments(entries: GalleryEntry[]) {
    const merged = [...attachments];
    for (const entry of entries) {
      if (merged.length >= AGENT_MAX_ATTACHMENTS) break;
      if (!merged.some((item) => item.id === entry.id)) merged.push(entry);
    }
    attachments = merged;
    attachOpen = false;
    void tick().then(() => textarea?.focus());
  }

  function removeAttachment(id: string) {
    attachments = attachments.filter((entry) => entry.id !== id);
  }
</script>

<form
  class="space-y-3 border-t border-stone-200 p-3 sm:p-4 dark:border-zinc-800"
  onsubmit={(event) => {
    event.preventDefault();
    void submit();
  }}
>
  {#if searchEnabled}
    <p class="text-xs text-stone-500 dark:text-zinc-400" role={searchUnavailable ? 'alert' : 'status'} data-testid="agent-search-capability">
      {searchUnavailable ? $t.agent.searchUnavailable : $t.agent.searchEnabled}
    </p>
  {/if}
  {#if error}
    <div class="status-error flex items-start justify-between gap-3 px-3 py-2 text-sm" role="alert" data-testid="agent-error">
      <span>{error}</span>
      <button type="button" class="ui-icon-button -my-2 -mr-2" aria-label={$t.agent.cancel} onclick={onDismissError}>×</button>
    </div>
  {/if}

  {#if attachments.length}
    <ul class="flex flex-wrap gap-2" aria-label={$t.agent.attachments}>
      {#each attachments as entry (entry.id)}
        <li class="relative w-14">
          <img src={thumbnailUrl(entry.filename, entry.thumbnail_url)} alt="" class="aspect-square w-full rounded-md border border-stone-300 object-cover dark:border-zinc-700" />
          <button
            type="button"
            class="control-focus absolute -right-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full border border-stone-300 bg-white text-xs text-stone-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
            aria-label={$t.agent.removeAttachment(entry.filename)}
            onclick={() => removeAttachment(entry.id)}
          >
            ×
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  <div class="relative">
    <label for="agent-prompt" class="sr-only">{$t.agent.composerLabel}</label>
    <textarea
      id="agent-prompt"
      bind:this={textarea}
      bind:value={text}
      rows="3"
      maxlength="8000"
      class="ui-field resize-y py-2.5 leading-6"
      placeholder={$t.agent.composerPlaceholder}
      role="combobox"
      aria-autocomplete="list"
      aria-expanded={listOpen}
      aria-controls="agent-mention-list"
      aria-activedescendant={activeOptionId}
      aria-describedby="agent-send-hint"
      oninput={() => {
        syncCaret();
        dismissedAt = null;
        activeIndex = 0;
        if (error) onDismissError();
      }}
      onkeydown={onKeydown}
      onkeyup={syncCaret}
      onclick={syncCaret}
      onfocus={syncCaret}
    ></textarea>

    {#if listOpen}
      <ul
        id="agent-mention-list"
        role="listbox"
        aria-label={$t.agent.mentionList}
        class="app-surface absolute bottom-full left-0 z-20 mb-2 max-h-60 w-full max-w-sm overflow-y-auto p-1"
      >
        {#each options as ref, index (ref.ref_label)}
          <li
            id="agent-mention-option-{index}"
            role="option"
            aria-selected={index === activeIndex}
            aria-label={$t.agent.mentionOption(ref.ref_label)}
            class="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-sm {index === activeIndex
              ? 'bg-stone-100 dark:bg-zinc-800'
              : ''}"
            onmousedown={(event) => {
              event.preventDefault();
              void chooseOption(ref);
            }}
          >
            {#if ref.filename}
              <img src={thumbnailUrl(ref.filename)} alt="" class="h-9 w-9 rounded object-cover" />
            {/if}
            <span class="min-w-0">
              <span class="block font-medium text-stone-900 dark:text-zinc-100">{optionLabel(ref)}</span>
              <span class="block truncate font-mono text-[11px] text-stone-500 dark:text-zinc-500">@{ref.ref_label}</span>
            </span>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <div class="flex flex-wrap items-center justify-between gap-2">
    <div class="flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="ui-button-secondary"
        disabled={attachments.length >= AGENT_MAX_ATTACHMENTS}
        onclick={() => (attachOpen = true)}
      >
        {$t.agent.attach}
      </button>
      <details class="relative">
        <summary class="ui-button-secondary cursor-pointer list-none">{$t.agent.imageSettings}</summary>
        <div class="app-surface absolute bottom-full left-0 z-20 mb-2 grid w-64 gap-3 p-3">
          <label class="block text-xs font-medium text-stone-600 dark:text-zinc-400">
            {$t.agent.size}
            <select class="form-select mt-1" bind:value={params.size} onchange={saveParams}>
              {#each SIZE_OPTIONS as size}
                <option value={size}>{size === 'auto' ? $t.agent.sizeAuto : size}</option>
              {/each}
            </select>
          </label>
          <label class="block text-xs font-medium text-stone-600 dark:text-zinc-400">
            {$t.agent.quality}
            <select class="form-select mt-1" bind:value={params.quality} onchange={saveParams}>
              {#each QUALITY_OPTIONS as quality}
                <option value={quality}>{quality}</option>
              {/each}
            </select>
          </label>
          <label class="block text-xs font-medium text-stone-600 dark:text-zinc-400">
            {$t.agent.format}
            <select class="form-select mt-1" bind:value={params.output_format} onchange={saveParams}>
              {#each FORMAT_OPTIONS as format}
                <option value={format}>{format}</option>
              {/each}
            </select>
          </label>
        </div>
      </details>
    </div>

    {#if turnActive}
      <button type="button" class="ui-button-secondary" disabled={cancelling} onclick={onStop} data-testid="agent-stop">
        {cancelling ? $t.agent.stopping : $t.agent.stop}
      </button>
    {:else}
      <button type="submit" class="ui-button-primary" disabled={!canSend} data-testid="agent-send">
        {sending ? $t.agent.sending : $t.agent.send}
      </button>
    {/if}
  </div>
  <p id="agent-send-hint" class="text-xs text-stone-500 dark:text-zinc-500">{$t.agent.sendHint}</p>
</form>

<AgentAttachDialog
  open={attachOpen}
  images={galleryImages}
  preselectedIds={gallerySelectedIds}
  max={AGENT_MAX_ATTACHMENTS - attachments.length}
  onConfirm={confirmAttachments}
  onClose={() => (attachOpen = false)}
/>
