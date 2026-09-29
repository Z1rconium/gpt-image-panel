<script lang="ts">
  import Overlay from '$lib/components/Overlay.svelte';
  import type { GalleryEntry } from '$lib/api/types/gallery';
  import { t } from '$lib/i18n';
  import { thumbnailUrl } from '$lib/utils/format';

  let {
    open,
    images,
    preselectedIds = [],
    max,
    onConfirm,
    onClose
  }: {
    open: boolean;
    images: GalleryEntry[];
    preselectedIds?: string[];
    max: number;
    onConfirm: (entries: GalleryEntry[]) => void;
    onClose: () => void;
  } = $props();

  let picked = $state<string[]>([]);

  $effect(() => {
    if (!open) return;
    const known = new Set(images.map((image) => image.id));
    picked = preselectedIds.filter((id) => known.has(id)).slice(0, max);
  });

  const atLimit = $derived(picked.length >= max);

  function toggle(id: string) {
    if (picked.includes(id)) picked = picked.filter((value) => value !== id);
    else if (!atLimit) picked = [...picked, id];
  }

  function confirm() {
    const byId = new Map(images.map((image) => [image.id, image]));
    onConfirm(picked.map((id) => byId.get(id)).filter((entry): entry is GalleryEntry => Boolean(entry)));
  }
</script>

<Overlay
  {open}
  {onClose}
  closeLabel={$t.agent.cancel}
  labelledBy="agent-attach-title"
  describedBy="agent-attach-hint"
  z={90}
  maxWidthClass="max-w-2xl"
  panelClass="flex max-h-[85dvh] flex-col overflow-hidden"
  mobileDvh
>
  <div class="border-b border-stone-200 p-5 dark:border-zinc-800">
    <h2 id="agent-attach-title" class="text-base font-semibold text-stone-950 dark:text-zinc-100">{$t.agent.attachTitle}</h2>
    <p id="agent-attach-hint" class="mt-1 text-xs text-stone-500 dark:text-zinc-500">
      {images.length ? $t.agent.attachLimit(max) : $t.agent.attachEmpty}
    </p>
  </div>
  <div class="min-h-0 flex-1 overflow-y-auto p-5">
    {#if images.length}
      <ul class="grid grid-cols-3 gap-3 sm:grid-cols-4">
        {#each images as image, index (image.id)}
          {@const checked = picked.includes(image.id)}
          <li>
            <label
              class="control-focus relative block cursor-pointer overflow-hidden rounded-lg border {checked
                ? 'border-emerald-500 ring-2 ring-emerald-400/50'
                : 'border-stone-200 dark:border-zinc-800'} {!checked && atLimit ? 'opacity-50' : ''}"
            >
              <input
                type="checkbox"
                class="sr-only"
                {checked}
                disabled={!checked && atLimit}
                data-autofocus={index === 0 ? '' : undefined}
                onchange={() => toggle(image.id)}
              />
              <img src={thumbnailUrl(image.filename, image.thumbnail_url)} alt={image.prompt.slice(0, 120)} loading="lazy" class="aspect-square w-full object-cover" />
            </label>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
  <div class="flex justify-end gap-2 border-t border-stone-200 p-5 dark:border-zinc-800">
    <button type="button" class="ui-button-secondary" onclick={onClose}>{$t.agent.cancel}</button>
    <button type="button" class="ui-button-primary" disabled={picked.length === 0} onclick={confirm}>
      {$t.agent.attachSelected(picked.length)}
    </button>
  </div>
</Overlay>
