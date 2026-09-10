<script lang="ts">
  import { t } from '$lib/i18n';
  import { IMAGE_MODEL_PRESETS, isImage25 } from '$lib/utils/imageModels';

  export let value = 'gpt-image-2';
  export let disabled = false;
  export let label = '';
  let custom = !IMAGE_MODEL_PRESETS.includes(value);
  let selected = custom ? '' : value;
  let previousValue = value;

  $: if (value !== previousValue) {
    custom = !IMAGE_MODEL_PRESETS.includes(value);
    selected = custom ? '' : value;
    previousValue = value;
  }

  function selectModel(next: string) {
    selected = next;
    custom = selected === '';
    if (!custom) value = selected;
  }
</script>

<div class="min-w-0">
  <label class="block">
    <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{label || $t.common.model}</span>
    <select value={selected} title={value} on:change={(event) => selectModel(event.currentTarget.value)} {disabled} class="control-focus form-select !bg-white font-mono dark:!bg-zinc-900">
      {#each IMAGE_MODEL_PRESETS as model}
        <option value={model}>{model}</option>
      {/each}
      <option value="">{$t.promptForm.customModel}</option>
    </select>
  </label>
  {#if custom}
    <input bind:value {disabled} title={value} aria-label={label || $t.common.model} placeholder="gpt-image-2.5-flare-2026-09-08" class="ui-field mt-2 w-full px-3 py-2 font-mono text-sm" />
  {/if}
  {#if isImage25(value)}
    <p class="mt-1 text-xs text-stone-500 dark:text-zinc-400">{value.includes('flare') ? $t.promptForm.flareHint : $t.promptForm.sunburstHint}</p>
  {/if}
</div>
