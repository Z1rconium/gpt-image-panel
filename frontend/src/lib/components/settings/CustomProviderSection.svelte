<script lang="ts">
  import { t } from '$lib/i18n';
  import type { ProviderKind } from '$lib/api/types/common';
  import type { ProviderTemplate } from '$lib/api/types/settings';
  import { formatProviderConfig, parseProviderConfigText } from '$lib/features/settings/draft';

  export let providerKind: ProviderKind = 'openai';
  export let providerConfigText = '';
  export let apiUrl = '';
  export let defaultModel = '';
  export let supportsMask = true;
  export let onLoadTemplates: () => Promise<ProviderTemplate[]> = async () => [];

  let loadingTemplate = false;
  let templateError = '';

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

  async function applyTemplate() {
    loadingTemplate = true;
    templateError = '';
    try {
      const template = (await onLoadTemplates())[0];
      if (!template) {
        templateError = $t.settings.providerTemplateMissing;
        return;
      }
      providerConfigText = formatProviderConfig(template.config);
      apiUrl = template.api_url;
      defaultModel = template.default_model;
      supportsMask = false;
    } catch {
      templateError = $t.settings.providerTemplateFailed;
    } finally {
      loadingTemplate = false;
    }
  }
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
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={loadingTemplate} on:click={applyTemplate}>
        {loadingTemplate ? $t.settings.providerTemplateLoading : $t.settings.providerLoadTemplate}
      </button>
      {#if templateError}<p role="alert" class="text-xs text-amber-600">{templateError}</p>{/if}
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.providerConfig}</span>
        <textarea bind:value={providerConfigText} rows="14" spellcheck="false" aria-invalid={configError ? 'true' : 'false'} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-xs text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"></textarea>
      </label>
      {#if configError}<p role="alert" class="text-xs text-amber-600">{configError}</p>{/if}
    </div>
  {/if}
</div>
