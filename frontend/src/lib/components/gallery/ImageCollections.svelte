<script lang="ts">
  import { t } from '$lib/i18n';
  import { galleryCollectionsStore } from '$lib/stores/galleryCollections';
  import { uiStore } from '$lib/stores/ui';

  let { imageId }: { imageId: string } = $props();

  const collections = $derived($galleryCollectionsStore.collections);
  let memberIds = $state(new Set<string>());
  let pendingId = $state('');
  let requestSequence = 0;

  $effect(() => {
    const currentImageId = imageId;
    const sequence = ++requestSequence;
    memberIds = new Set();
    void galleryCollectionsStore.load().catch(() => {});
    galleryCollectionsStore
      .imageCollectionIds(currentImageId)
      .then((ids) => {
        if (sequence === requestSequence) memberIds = new Set(ids);
      })
      .catch(() => {});
  });

  async function toggle(collectionId: string) {
    if (pendingId) return;
    pendingId = collectionId;
    const member = memberIds.has(collectionId);
    try {
      if (member) await galleryCollectionsStore.removeItems(collectionId, { ids: [imageId] });
      else await galleryCollectionsStore.addItems(collectionId, { ids: [imageId] });
      const next = new Set(memberIds);
      if (member) next.delete(collectionId);
      else next.add(collectionId);
      memberIds = next;
    } catch (error) {
      uiStore.showToast(error instanceof Error ? error.message : $t.messages.requestFailed, 'error');
    } finally {
      pendingId = '';
    }
  }
</script>

{#if collections.length}
  <section aria-labelledby={`image-collections-${imageId}`}>
    <div id={`image-collections-${imageId}`} class="mb-1.5 text-xs font-medium text-stone-500 dark:text-zinc-500">{$t.collections.title}</div>
    <div class="flex flex-wrap gap-1.5">
      {#each collections as collection (collection.id)}
        <button
          type="button"
          class={`control-focus rounded-full border px-2.5 py-1 text-xs transition-colors disabled:opacity-50 ${
            memberIds.has(collection.id)
              ? 'border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200'
              : 'border-stone-300 text-stone-600 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800'
          }`}
          aria-pressed={memberIds.has(collection.id)}
          disabled={Boolean(pendingId)}
          onclick={() => void toggle(collection.id)}
        >
          {collection.name}
        </button>
      {/each}
    </div>
  </section>
{/if}
