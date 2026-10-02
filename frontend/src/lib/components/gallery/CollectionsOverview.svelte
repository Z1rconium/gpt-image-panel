<script lang="ts">
  import type { GalleryCollection } from '$lib/api/types/gallery';
  import { t } from '$lib/i18n';
  import { galleryActivityStore, galleryStore } from '$lib/stores/gallery';
  import { uiStore } from '$lib/stores/ui';
  import { thumbnailUrl } from '$lib/utils/format';
  import Download from 'lucide-svelte/icons/download';
  import FolderOpen from 'lucide-svelte/icons/folder-open';

  interface Props {
    collections: GalleryCollection[];
    activeCollectionId: string | null;
    onOpenCollection: (collectionId: string) => void;
    onManageCollections: () => void;
  }

  let { collections, activeCollectionId, onOpenCollection, onManageCollections }: Props = $props();

  let failedCoverIds = $state<string[]>([]);

  const busy = $derived(Boolean($galleryActivityStore.operationStatus));

  function coverSrc(collection: GalleryCollection) {
    if (!collection.cover_filename || failedCoverIds.includes(collection.cover_filename)) return '';
    return thumbnailUrl(collection.cover_filename);
  }

  function handleCoverError(collection: GalleryCollection) {
    if (collection.cover_filename && !failedCoverIds.includes(collection.cover_filename)) failedCoverIds = [...failedCoverIds, collection.cover_filename].slice(-100);
  }

  async function downloadZip(collection: GalleryCollection) {
    try {
      await galleryStore.exportCollection(collection.id, collection.image_count, (message) => uiStore.showToast(message));
    } catch (error) {
      uiStore.showToast(error instanceof Error ? error.message : $t.messages.requestFailed, 'error');
    }
  }
</script>

<section class="mb-4 rounded-xl border border-stone-200 bg-white/85 p-3 dark:border-zinc-800 dark:bg-zinc-950/45" aria-label={$t.collections.title}>
  <div class="mb-3 flex items-center justify-between gap-2">
    <h3 class="text-xs font-semibold text-stone-800 dark:text-zinc-200">{$t.collections.title}</h3>
    <button
      type="button"
      class="control-focus rounded-lg border border-stone-300 px-2.5 py-1.5 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
      onclick={onManageCollections}
    >
      {$t.collections.manageTitle}
    </button>
  </div>
  {#if !collections.length}
    <p class="rounded-lg border border-dashed border-stone-300 px-3 py-6 text-center text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-500">
      {$t.collections.empty}
    </p>
  {:else}
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {#each collections as collection (collection.id)}
        <div class={`relative overflow-hidden rounded-xl border ${activeCollectionId === collection.id ? 'border-emerald-400 ring-1 ring-emerald-400/40' : 'border-stone-200 dark:border-zinc-800'}`}>
          <button
            type="button"
            class="control-focus block w-full text-left"
            aria-label={`${collection.name} · ${$t.collections.imageCount(collection.image_count)}`}
            aria-pressed={activeCollectionId === collection.id}
            onclick={() => onOpenCollection(collection.id)}
          >
            <span class="relative block aspect-[4/3] bg-stone-100 dark:bg-zinc-900">
              {#if coverSrc(collection)}
                <img
                  src={coverSrc(collection)}
                  alt=""
                  class="h-full w-full object-cover"
                  loading="lazy"
                  decoding="async"
                  onerror={() => handleCoverError(collection)}
                />
              {:else}
                <span class="flex h-full w-full items-center justify-center text-stone-400 dark:text-zinc-600">
                  <FolderOpen class="h-8 w-8" aria-hidden="true" />
                </span>
              {/if}
              {#if collection.is_default}
                <span class="absolute right-1.5 top-1.5 rounded bg-emerald-600/90 px-1.5 py-0.5 text-xs font-medium text-white">{$t.collections.default}</span>
              {/if}
            </span>
            <span class="flex items-center justify-between gap-2 px-2.5 py-2">
              <span class="min-w-0 truncate text-xs font-medium text-stone-800 dark:text-zinc-200">{collection.name}</span>
              <span class="shrink-0 text-xs text-stone-500 dark:text-zinc-500">{$t.collections.imageCount(collection.image_count)}</span>
            </span>
          </button>
          <button
            type="button"
            class="gallery-icon-action control-focus absolute left-1.5 top-1.5 border-stone-300 bg-white/90 text-stone-700 hover:bg-stone-100 disabled:opacity-30 dark:border-zinc-700 dark:bg-zinc-950/80 dark:text-zinc-300 dark:hover:bg-zinc-800"
            aria-label={$t.collections.downloadZip(collection.name)}
            title={$t.collections.downloadZip(collection.name)}
            disabled={busy || !collection.image_count}
            onclick={() => void downloadZip(collection)}
          >
            <Download aria-hidden="true" />
          </button>
        </div>
      {/each}
    </div>
  {/if}
</section>
