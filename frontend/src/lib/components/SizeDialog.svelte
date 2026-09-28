<script lang="ts">
  import Overlay from '$lib/components/Overlay.svelte';
  import { promptForm } from '$lib/features/workspace/formState.svelte';
  import { t } from '$lib/i18n';
  import { preferencesStore } from '$lib/stores/preferences';
  import { validImageSize } from '$lib/utils/imageModels';
  import {
    BUILT_IN_SIZES,
    MAX_SIZE_PRESET_NAME_LENGTH,
    SIZE_TIERS,
    addNamedSizePreset,
    groupSizesByTier
  } from '$lib/utils/sizePresets';

  interface Props {
    open?: boolean;
    onClose?: () => void;
  }

  let { open = false, onClose = () => {} }: Props = $props();

  const groups = groupSizesByTier(BUILT_IN_SIZES);
  const value = $derived(promptForm.size);
  let custom = $state('');
  let error = $state(false);
  let presetName = $state('');
  let presetError = $state('');

  $effect(() => {
    if (open) {
      custom = promptForm.size;
      error = false;
      presetName = '';
      presetError = '';
    }
  });

  function apply(size = custom.trim()) {
    error = !validImageSize(size);
    if (error) return;
    promptForm.size = size;
    onClose();
  }

  function savePreset() {
    const size = (custom.trim() || promptForm.size).toLowerCase();
    const result = addNamedSizePreset($preferencesStore.sizePresets, presetName, size);
    if (!result.ok) {
      const messages = $t.sizeDialog.presetErrors;
      presetError = messages[result.reason];
      return;
    }
    preferencesStore.patch({ sizePresets: result.presets });
    presetName = '';
    presetError = '';
  }

  function deletePreset(name: string) {
    preferencesStore.patch({ sizePresets: $preferencesStore.sizePresets.filter((preset) => preset.name !== name) });
  }

  function sizeButtonClass(size: string) {
    return `control-focus rounded-lg border px-3 py-2.5 text-sm transition-colors ${
      value === size
        ? 'border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:text-emerald-100'
        : 'border-stone-200 bg-stone-50 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300 dark:hover:bg-zinc-800'
    }`;
  }
</script>

<Overlay {open} {onClose} closeLabel={$t.common.close} labelledBy="size-dialog-title" z={80} maxWidthClass="max-w-lg" panelClass="p-5" backdropBlur>
  <div class="mb-5 flex items-center justify-between">
    <div>
      <h2 id="size-dialog-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.sizeDialog.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.sizeDialog.subtitle}</p>
    </div>
    <button type="button" class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.common.close} onclick={onClose}>x</button>
  </div>

  <div class="max-h-[60dvh] space-y-4 overflow-y-auto pr-1">
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
      <button type="button" class={sizeButtonClass('auto')} onclick={() => apply('auto')}>auto</button>
    </div>

    {#each SIZE_TIERS as tier}
      <section aria-labelledby={`size-tier-${tier}`}>
        <h3 id={`size-tier-${tier}`} class="mb-2 text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-zinc-400">{tier}</h3>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {#each groups[tier] as size}
            <button type="button" class={sizeButtonClass(size)} onclick={() => apply(size)}>{size}</button>
          {/each}
        </div>
      </section>
    {/each}

    <section aria-labelledby="size-tier-custom">
      <h3 id="size-tier-custom" class="mb-2 text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-zinc-400">{$t.sizeDialog.myPresets}</h3>
      {#if $preferencesStore.sizePresets.length}
        <ul class="grid gap-2 sm:grid-cols-2">
          {#each $preferencesStore.sizePresets as preset (preset.name)}
            <li class="flex min-w-0 items-stretch gap-1">
              <button type="button" class={`${sizeButtonClass(preset.size)} min-w-0 flex-1 truncate text-left`} onclick={() => apply(preset.size)}>
                {preset.name} · {preset.size}
              </button>
              <button
                type="button"
                class="control-focus rounded-lg border border-stone-200 px-2 text-xs text-stone-500 hover:bg-stone-100 hover:text-red-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800"
                aria-label={$t.sizeDialog.deletePreset(preset.name)}
                title={$t.sizeDialog.deletePreset(preset.name)}
                onclick={() => deletePreset(preset.name)}
              >x</button>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="text-xs text-stone-500 dark:text-zinc-500">{$t.sizeDialog.noPresets}</p>
      {/if}
    </section>
  </div>

  {#if error}<p role="alert" class="mt-3 text-xs text-red-600">{$t.sizeDialog.invalidSize}</p>{/if}
  <p class="mt-3 text-xs text-stone-500">{$t.sizeDialog.experimentalSize}</p>
  <div class="mt-4 flex gap-2">
    <label for="custom-size" class="sr-only">{$t.common.size}</label>
    <input
      bind:value={custom}
      id="custom-size"
      name="custom_size"
      inputmode="numeric"
      autocomplete="off"
      aria-label={$t.common.size}
      class="control-focus min-w-0 flex-1 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
      placeholder="1024x1024"
    />
    <button type="button" class="control-focus rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500" onclick={() => apply()}>{$t.common.apply}</button>
  </div>
  <div class="mt-2 flex gap-2">
    <label for="size-preset-name" class="sr-only">{$t.sizeDialog.presetName}</label>
    <input
      bind:value={presetName}
      id="size-preset-name"
      name="size_preset_name"
      autocomplete="off"
      maxlength={MAX_SIZE_PRESET_NAME_LENGTH}
      aria-label={$t.sizeDialog.presetName}
      placeholder={$t.sizeDialog.presetName}
      class="control-focus min-w-0 flex-1 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
      onkeydown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          savePreset();
        }
      }}
    />
    <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800" onclick={savePreset}>{$t.sizeDialog.savePreset}</button>
  </div>
  {#if presetError}<p role="alert" class="mt-2 text-xs text-red-600">{presetError}</p>{/if}
</Overlay>
