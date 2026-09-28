<script lang="ts">
  import Overlay from '$lib/components/Overlay.svelte';
  import { t } from '$lib/i18n';
  import { preferencesStore } from '$lib/stores/preferences';

  interface Props {
    open?: boolean;
    onClose?: () => void;
  }

  let { open = false, onClose = () => {} }: Props = $props();
</script>

<Overlay {open} {onClose} closeLabel={$t.common.close} labelledBy="preferences-dialog-title" z={80} maxWidthClass="max-w-md" panelClass="p-5" backdropBlur>
  <div class="mb-4 flex items-start justify-between gap-3">
    <div>
      <h2 id="preferences-dialog-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.preferences.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.preferences.subtitle}</p>
    </div>
    <button type="button" class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.common.close} onclick={onClose}>x</button>
  </div>

  <div class="space-y-2">
    <label class="flex items-start gap-2.5 rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 dark:border-zinc-700 dark:bg-zinc-950">
      <input
        type="checkbox"
        class="control-focus mt-0.5 accent-emerald-500"
        checked={$preferencesStore.clearPromptAfterSubmit}
        onchange={(event) => preferencesStore.patch({ clearPromptAfterSubmit: event.currentTarget.checked })}
      />
      <span class="min-w-0">
        <span class="block text-sm text-stone-800 dark:text-zinc-200">{$t.preferences.clearPromptAfterSubmit}</span>
        <span class="mt-1 block text-xs text-stone-500 dark:text-zinc-500">{$t.preferences.clearPromptAfterSubmitHint}</span>
      </span>
    </label>
    <label class="flex items-start gap-2.5 rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 dark:border-zinc-700 dark:bg-zinc-950">
      <input
        type="checkbox"
        class="control-focus mt-0.5 accent-emerald-500"
        checked={$preferencesStore.retryUsesJobPreset}
        onchange={(event) => preferencesStore.patch({ retryUsesJobPreset: event.currentTarget.checked })}
      />
      <span class="min-w-0">
        <span class="block text-sm text-stone-800 dark:text-zinc-200">{$t.preferences.retryUsesJobPreset}</span>
        <span class="mt-1 block text-xs text-stone-500 dark:text-zinc-500">{$t.preferences.retryUsesJobPresetHint}</span>
      </span>
    </label>
  </div>
  <p class="mt-4 text-xs text-stone-500 dark:text-zinc-500">{$t.preferences.sizePresetsHint}</p>
</Overlay>
