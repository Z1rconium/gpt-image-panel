<script lang="ts">
  import { t } from '$lib/i18n';
  import type { ProviderKind } from '$lib/api/types/common';
  import { parseProviderConfigText } from '$lib/features/settings/draft';

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
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.providerConfig}</span>
        <textarea bind:value={providerConfigText} rows="14" spellcheck="false" placeholder={MAPPING_PLACEHOLDER} aria-invalid={configError ? 'true' : 'false'} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-xs text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"></textarea>
      </label>
      {#if configError}<p role="alert" class="text-xs text-amber-600">{configError}</p>{/if}
    </div>
  {/if}
</div>
