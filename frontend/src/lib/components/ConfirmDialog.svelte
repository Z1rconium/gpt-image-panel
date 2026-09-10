<script lang="ts">
  import Overlay from '$lib/components/Overlay.svelte';
  import { confirmStore, type ConfirmRequest } from '$lib/stores/confirm';

  export let request: ConfirmRequest | null = null;

  // Keeps the last request visible while Overlay's close transition plays,
  // since confirmStore nulls `request` the instant cancel()/accept() fires.
  let displayRequest: ConfirmRequest | null = null;
  $: if (request) displayRequest = request;

  let requiredValue = '';
  let lastRequestId = 0;

  $: if ((request?.id || 0) !== lastRequestId) {
    requiredValue = '';
    lastRequestId = request?.id || 0;
  }
  $: requiredText = displayRequest?.requiredText || '';
  $: canConfirm = !requiredText || requiredValue === requiredText;
  $: confirmClass =
    displayRequest?.variant === 'danger'
      ? 'bg-red-600 text-white hover:bg-red-500 disabled:bg-red-900 disabled:text-red-200'
      : 'bg-emerald-600 text-white hover:bg-emerald-500 disabled:bg-emerald-900 disabled:text-emerald-200';
</script>

<Overlay
  open={Boolean(request)}
  onClose={() => confirmStore.cancel()}
  closeLabel={displayRequest?.closeLabel ?? ''}
  labelledBy="confirm-dialog-title"
  z={95}
  maxWidthClass="max-w-md"
  panelClass="flex flex-col overflow-hidden"
  mobileDvh
>
  {#if displayRequest}
    <div class="shrink-0 border-b border-stone-200 p-5 dark:border-zinc-800">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 id="confirm-dialog-title" class="text-base font-semibold text-stone-950 dark:text-zinc-100">{displayRequest.title}</h2>
          <p class="mt-2 text-sm leading-6 text-stone-600 dark:text-zinc-400">{displayRequest.message}</p>
        </div>
        <button type="button" class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={displayRequest.closeLabel} on:click={() => confirmStore.cancel()}>
          x
        </button>
      </div>
    </div>

    {#if displayRequest.details?.length || requiredText}
      <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        {#if displayRequest.details?.length}
          <ul class="space-y-2 text-sm text-stone-700 dark:text-zinc-300">
            {#each displayRequest.details as detail}
              <li class="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/60">{detail}</li>
            {/each}
          </ul>
        {/if}

        {#if requiredText}
          <label class="block">
            <span class="text-xs font-medium text-stone-500 dark:text-zinc-500">{displayRequest.requiredTextLabel}</span>
            <input
              bind:value={requiredValue}
              class="control-focus mt-2 w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-900 focus:border-red-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
              autocomplete="off"
              spellcheck="false"
              data-autofocus
            />
          </label>
        {/if}
      </div>
    {/if}

    <div class="shrink-0 flex flex-col gap-3 border-t border-stone-200 p-5 dark:border-zinc-800 sm:flex-row sm:justify-end">
      <button type="button" class="control-focus w-full rounded-lg border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 sm:w-auto" on:click={() => confirmStore.cancel()}>
        {displayRequest.cancelLabel}
      </button>
      <button type="button" disabled={!canConfirm} class={`control-focus w-full rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto ${confirmClass}`} on:click={() => confirmStore.accept()}>
        {displayRequest.confirmLabel}
      </button>
    </div>
  {/if}
</Overlay>
