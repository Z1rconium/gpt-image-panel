<script lang="ts">
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { dialog } from '$lib/actions/dialog';
  import { t } from '$lib/i18n';
  import { settingsStore } from '$lib/stores/settings';
  import { uiStore } from '$lib/stores/ui';
  import type {
    PresetImportInstruction,
    PresetImportPreviewResponse,
    SettingsResponse
  } from '$lib/api/types/settings';

  export let open = false;
  export let settings: SettingsResponse | null = null;
  export let initialPackage: unknown = null;
  export let onClose: () => void = () => {};
  export let onApplied: () => void = () => {};

  type ImportAction = 'create' | 'update' | 'skip';

  let packageText = '';
  let parsedPackage: unknown = null;
  let preview: PresetImportPreviewResponse | null = null;
  let errorMessage = '';
  let previewing = false;
  let applying = false;
  let actions: Record<number, { action: ImportAction; targetId: string }> = {};
  let loadedInitial = false;

  function resetState() {
    packageText = '';
    parsedPackage = null;
    preview = null;
    errorMessage = '';
    actions = {};
    loadedInitial = false;
  }

  $: canApply = Boolean(preview?.valid) && !previewing && !applying;

  function defaultActionFor(index: number): { action: ImportAction; targetId: string } {
    const item = preview?.items.find((candidate) => candidate.index === index);
    return { action: 'create', targetId: item?.duplicate_preset_id || '' };
  }

  async function previewPackage(packageValue: unknown) {
    previewing = true;
    errorMessage = '';
    try {
      const result = await settingsStore.previewPresetImport(packageValue);
      preview = result;
      parsedPackage = packageValue;
      actions = {};
      for (const item of result.items) {
        actions[item.index] = defaultActionFor(item.index);
      }
    } catch (error) {
      preview = null;
      errorMessage = error instanceof Error ? error.message : String(error);
    } finally {
      previewing = false;
    }
  }

  async function previewFromText() {
    errorMessage = '';
    if (!packageText.trim()) {
      errorMessage = $t.settings.importNeedsInput;
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(packageText);
    } catch {
      errorMessage = $t.settings.providerConfigInvalidJson;
      return;
    }
    await previewPackage(parsed);
  }

  async function onFileSelected(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    packageText = await file.text();
    await previewFromText();
    input.value = '';
  }

  async function applyImport() {
    if (!preview?.valid || parsedPackage === null) return;
    const instructions: PresetImportInstruction[] = [];
    for (const item of preview.items) {
      const choice = actions[item.index] || defaultActionFor(item.index);
      if (choice.action === 'skip') {
        instructions.push({ index: item.index, action: 'skip' });
        continue;
      }
      if (choice.action === 'update') {
        if (!choice.targetId) {
          errorMessage = $t.settings.importUpdateTargetRequired;
          return;
        }
        instructions.push({
          index: item.index,
          action: 'update',
          target_preset_id: choice.targetId
        });
        continue;
      }
      instructions.push({ index: item.index, action: 'create' });
    }
    applying = true;
    errorMessage = '';
    try {
      await settingsStore.importPresets(parsedPackage, instructions, uiStore.showToast);
      onApplied();
      resetState();
      onClose();
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : String(error);
    } finally {
      applying = false;
    }
  }

  $: if (open && !loadedInitial) {
    loadedInitial = true;
    errorMessage = '';
    if (initialPackage && typeof initialPackage === 'object') {
      packageText = JSON.stringify(initialPackage, null, 2);
      void previewPackage(initialPackage);
    }
  }
  $: if (!open && loadedInitial) {
    resetState();
  }
</script>

{#if open}
  <div class="mobile-dialog-root fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" in:overlayIn out:overlayOut>
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.importClose} on:click={onClose}></button>
    <div
      class="mobile-dvh-dialog overlay-panel relative flex max-h-[88vh] w-full max-w-3xl flex-col rounded-xl border border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
      in:dialogIn
      out:dialogOut
      aria-labelledby="preset-import-dialog-title"
      use:dialog={{ open, onClose }}
    >
      <div class="flex items-start justify-between gap-4 border-b border-stone-200 p-5 dark:border-zinc-800">
        <div class="min-w-0">
          <h2 id="preset-import-dialog-title" class="text-base font-semibold text-stone-900 dark:text-zinc-100">{$t.settings.importTitle}</h2>
          <p class="mt-1 text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.settings.importHint}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.settings.importClose} on:click={onClose}>x</button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-5">
        <div class="flex flex-wrap items-center gap-3">
          <label class="control-focus cursor-pointer rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">
            {$t.settings.importChooseFile}
            <input class="hidden" type="file" accept="application/json,.json" on:change={onFileSelected} />
          </label>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={previewFromText}>
            {$t.settings.importPreview}
          </button>
          {#if previewing}<span class="text-xs text-stone-500 dark:text-zinc-400">{$t.settings.importPreviewing}</span>{/if}
        </div>

        <label class="mt-3 block">
          <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.importPasteLabel}</span>
          <textarea
            bind:value={packageText}
            rows="6"
            spellcheck="false"
            class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-xs text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            placeholder={$t.settings.importPastePlaceholder}
          ></textarea>
        </label>

        {#if errorMessage}
          <div role="alert" class="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-200">{errorMessage}</div>
        {/if}

        {#if preview}
          {#if !preview.valid}
            <div class="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 p-3">
              <p class="text-xs font-semibold text-red-700 dark:text-red-200">{$t.settings.importInvalid}</p>
              <ul class="mt-2 space-y-1 font-mono text-[11px] text-red-700 dark:text-red-200">
                {#each preview.errors as issue}
                  <li>{issue.path}: {issue.message}</li>
                {/each}
              </ul>
            </div>
          {:else}
            <ul class="mt-4 space-y-3">
              {#each preview.items as item (item.index)}
                <li class="rounded-lg border border-stone-200 p-3 dark:border-zinc-800">
                  <div class="flex flex-wrap items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="truncate text-sm font-medium text-stone-900 dark:text-zinc-100">{item.name}</div>
                      <div class="mt-1 truncate font-mono text-xs text-stone-500 dark:text-zinc-500">{item.api_url}</div>
                    </div>
                    <select
                      class="control-focus rounded-lg border border-stone-300 bg-stone-50 px-2 py-1 text-xs text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                      value={actions[item.index]?.action || 'create'}
                      on:change={(event) => { actions = { ...actions, [item.index]: { ...(actions[item.index] || defaultActionFor(item.index)), action: event.currentTarget.value as ImportAction } }; }}
                    >
                      <option value="create">{$t.settings.importActionCreate}</option>
                      <option value="update" disabled={!item.duplicate_preset_id}>{$t.settings.importActionUpdate}</option>
                      <option value="skip">{$t.settings.importActionSkip}</option>
                    </select>
                  </div>
                  {#if item.duplicate_preset_name}
                    <p class="mt-2 text-xs text-amber-700 dark:text-amber-300">{$t.settings.importDuplicate(item.duplicate_preset_name)}</p>
                  {/if}
                  {#if actions[item.index]?.action === 'update'}
                    <label class="mt-2 block">
                      <span class="mb-1 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.importUpdateTarget}</span>
                      <select
                        class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-2 py-1.5 text-xs text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                        value={actions[item.index]?.targetId || item.duplicate_preset_id || ''}
                        on:change={(event) => { actions = { ...actions, [item.index]: { ...(actions[item.index] || defaultActionFor(item.index)), targetId: event.currentTarget.value } }; }}
                      >
                        <option value="">{$t.settings.importUpdateTargetSelect}</option>
                        {#each settings?.presets || [] as preset}
                          <option value={preset.id}>{preset.name || preset.id}</option>
                        {/each}
                      </select>
                    </label>
                  {/if}
                  {#if item.warnings.length}
                    <ul class="mt-2 space-y-1 text-[11px] text-stone-500 dark:text-zinc-500">
                      {#each item.warnings as warning}<li>{warning}</li>{/each}
                    </ul>
                  {/if}
                </li>
              {/each}
            </ul>
          {/if}
        {/if}
      </div>

      <div class="flex justify-end gap-3 border-t border-stone-200 p-5 dark:border-zinc-800">
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={onClose}>
          {$t.common.close}
        </button>
        <button
          type="button"
          disabled={!canApply}
          class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
          on:click={applyImport}
        >
          {applying ? $t.settings.importApplying : $t.settings.importApply}
        </button>
      </div>
    </div>
  </div>
{/if}
