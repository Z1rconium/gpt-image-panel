<script lang="ts">
  import { t } from '$lib/i18n';
  import { galleryCollectionsStore } from '$lib/stores/galleryCollections';
  import { uiStore } from '$lib/stores/ui';
  import ArrowDown from 'lucide-svelte/icons/arrow-down';
  import ArrowLeft from 'lucide-svelte/icons/arrow-left';
  import ArrowUp from 'lucide-svelte/icons/arrow-up';
  import X from 'lucide-svelte/icons/x';
  import type { GalleryCollection, GalleryEntry } from '$lib/api/types/gallery';

  interface Props {
    collection: GalleryCollection;
    onBack: () => void;
    onClose: () => void;
  }

  let { collection, onBack, onClose }: Props = $props();

  let memberImages = $state<GalleryEntry[]>([]);
  let loadingMembers = $state(false);
  let unsupportedSkipped = $state(0);
  let busy = $state(false);

  $effect(() => {
    void loadMembers(collection.id);
  });

  function showError(error: unknown) {
    uiStore.showToast(error instanceof Error ? error.message : $t.messages.requestFailed, 'error');
  }

  async function loadMembers(id: string) {
    loadingMembers = true;
    memberImages = [];
    unsupportedSkipped = 0;
    try {
      const res = await galleryCollectionsStore.loadReferences(id);
      memberImages = res.images;
      unsupportedSkipped = res.skipped_unsupported;
    } catch (e) {
      showError(e);
      onBack();
    } finally {
      loadingMembers = false;
    }
  }

  async function moveMember(fromIndex: number, toIndex: number) {
    if (busy || fromIndex === toIndex) return;
    if (fromIndex < 0 || toIndex < 0 || fromIndex >= memberImages.length || toIndex >= memberImages.length) return;
    const next = [...memberImages];
    const [moved] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, moved);
    memberImages = next;
    busy = true;
    try {
      await galleryCollectionsStore.reorderItems(
        collection.id,
        next.map((img) => img.id)
      );
      uiStore.showToast($t.collections.itemOrderUpdated, 'status');
    } catch (e) {
      showError(e);
      // Reload on failure to restore consistent server order
      void loadMembers(collection.id);
    } finally {
      busy = false;
    }
  }
</script>

<div class="mb-4 flex items-center justify-between gap-3">
  <div class="flex items-center gap-2">
    <button
      type="button"
      class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
      onclick={onBack}
      title={$t.collections.manageTitle}
      aria-label={$t.collections.manageTitle}
    >
      <ArrowLeft class="h-4 w-4" />
    </button>
    <div>
      <h2 id="collections-dialog-title" class="text-base font-semibold text-stone-950 dark:text-zinc-100">
        {$t.collections.memberOrderTitle(collection.name)}
      </h2>
      <p class="mt-0.5 text-xs text-stone-500 dark:text-zinc-500">
        {$t.collections.memberOrderSubtitle}
      </p>
    </div>
  </div>
  <button
    type="button"
    class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
    aria-label={$t.common.close}
    onclick={onClose}
  >
    <X class="h-4 w-4" />
  </button>
</div>

{#if unsupportedSkipped > 0}
  <div class="mb-3 rounded-lg border border-amber-300/80 bg-amber-50 p-2.5 text-xs text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200">
    {$t.collections.unsupportedImagesNotice(unsupportedSkipped)}
  </div>
{/if}

{#if loadingMembers}
  <p class="rounded-lg border border-dashed border-stone-300 px-3 py-8 text-center text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-500">
    {$t.gallery.loading}
  </p>
{:else if !memberImages.length}
  <p class="rounded-lg border border-dashed border-stone-300 px-3 py-8 text-center text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-500">
    {$t.collections.emptyMembers}
  </p>
{:else}
  <ul class="max-h-[55dvh] space-y-1.5 overflow-y-auto pr-1">
    {#each memberImages as img, index (img.id)}
      <li class="flex items-center gap-2.5 rounded-lg border border-stone-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-950/60">
        <span class="shrink-0 rounded bg-stone-100 px-1.5 py-0.5 text-xs font-mono font-medium text-stone-600 dark:bg-zinc-800 dark:text-zinc-400">
          #{index + 1}
        </span>
        <img
          src={img.thumbnail_url || img.image_url}
          alt=""
          class="h-10 w-10 shrink-0 rounded object-cover border border-stone-200 dark:border-zinc-800 bg-stone-100 dark:bg-zinc-900"
        />
        <div class="min-w-0 flex-1">
          <p class="truncate text-xs font-medium text-stone-800 dark:text-zinc-200">
            {img.prompt || img.filename}
          </p>
          <p class="truncate font-mono text-[10px] text-stone-400 dark:text-zinc-500">
            {img.id}
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-1">
          <button
            type="button"
            class="gallery-icon-action control-focus border-stone-300 text-stone-600 hover:bg-stone-100 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            aria-label={$t.collections.moveUp(img.filename)}
            title={$t.collections.moveUp(img.filename)}
            disabled={busy || index === 0}
            onclick={() => moveMember(index, index - 1)}
          >
            <ArrowUp class="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="gallery-icon-action control-focus border-stone-300 text-stone-600 hover:bg-stone-100 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            aria-label={$t.collections.moveDown(img.filename)}
            title={$t.collections.moveDown(img.filename)}
            disabled={busy || index === memberImages.length - 1}
            onclick={() => moveMember(index, index + 1)}
          >
            <ArrowDown class="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </li>
    {/each}
  </ul>
{/if}
