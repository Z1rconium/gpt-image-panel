<script lang="ts">
  import Overlay from '$lib/components/Overlay.svelte';
  import { t } from '$lib/i18n';
  import { galleryCollectionsStore } from '$lib/stores/galleryCollections';
  import {
    editSourceStore,
    editSourceCount,
    MAX_EDIT_SOURCE_IMAGES,
    type EditGalleryReferenceSource
  } from '$lib/stores/editSource';
  import { uiStore } from '$lib/stores/ui';
  import type { GalleryCollection, GalleryEntry } from '$lib/api/types/gallery';
  import Check from 'lucide-svelte/icons/check';
  import Package from 'lucide-svelte/icons/package';
  import X from 'lucide-svelte/icons/x';

  interface Props {
    open?: boolean;
    onClose?: () => void;
    onAdd?: (items: EditGalleryReferenceSource[]) => void;
  }

  let { open = false, onClose = () => {}, onAdd = () => {} }: Props = $props();

  const collections = $derived($galleryCollectionsStore.collections);
  const prioritizedCollections = $derived.by(() => {
    return [...collections].sort((a, b) => {
      if (Boolean(a.asset_alias) && !b.asset_alias) return -1;
      if (!a.asset_alias && Boolean(b.asset_alias)) return 1;
      return 0;
    });
  });

  const availableSlots = $derived(
    Math.max(0, MAX_EDIT_SOURCE_IMAGES - editSourceCount($editSourceStore))
  );

  let selectedCollectionId = $state<string>('');
  let memberImages = $state<GalleryEntry[]>([]);
  let skippedUnsupported = $state(0);
  let loadingMembers = $state(false);
  let selectedIds = $state<Set<string>>(new Set());
  let loadSeq = 0;

  $effect(() => {
    if (open) {
      const seq = ++loadSeq;
      void galleryCollectionsStore.load(false).then(() => {
        if (seq !== loadSeq || !open) return;
        const list = prioritizedCollections;
        if (!selectedCollectionId || !list.some((c) => c.id === selectedCollectionId)) {
          if (list.length > 0) {
            void selectCollection(list[0].id);
          }
        } else {
          void selectCollection(selectedCollectionId);
        }
      });
    } else {
      loadSeq += 1;
      loadingMembers = false;
      selectedIds = new Set();
    }
  });

  async function selectCollection(id: string) {
    const seq = ++loadSeq;
    selectedCollectionId = id;
    loadingMembers = true;
    memberImages = [];
    skippedUnsupported = 0;
    selectedIds = new Set();

    try {
      const res = await galleryCollectionsStore.loadReferences(id);
      if (seq !== loadSeq) return;
      memberImages = res.images;
      skippedUnsupported = res.skipped_unsupported;
      // Pre-select all available references by default
      selectedIds = new Set(res.images.map((img) => img.id));
    } catch (e) {
      if (seq !== loadSeq) return;
      uiStore.showToast(e instanceof Error ? e.message : $t.messages.requestFailed, 'error');
    } finally {
      if (seq === loadSeq) loadingMembers = false;
    }
  }

  function toggleSelect(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    selectedIds = next;
  }

  function selectAll() {
    selectedIds = new Set(memberImages.map((img) => img.id));
  }

  function deselectAll() {
    selectedIds = new Set();
  }

  function handleAdd() {
    const selectedList = memberImages.filter((img) => selectedIds.has(img.id));
    if (!selectedList.length) return;

    const refs: EditGalleryReferenceSource[] = selectedList.map((img) => ({
      id: `gallery-ref-${img.id}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      imageId: img.id,
      filename: img.filename,
      label: img.prompt || img.filename,
      previewUrl: img.image_url || img.thumbnail_url || '',
      previewLabel: img.prompt || img.filename,
      width: img.image_width || 0,
      height: img.image_height || 0
    }));

    onAdd(refs);
    onClose();
  }
</script>

<Overlay
  {open}
  {onClose}
  closeLabel={$t.common.close}
  labelledBy="asset-pack-picker-title"
  describedBy="asset-pack-picker-subtitle"
  z={85}
  maxWidthClass="max-w-2xl"
  panelClass="p-5 flex flex-col max-h-[85dvh]"
  backdropBlur
>
  <div class="mb-3 flex items-start justify-between gap-3">
    <div class="flex items-center gap-2">
      <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400">
        <Package class="h-5 w-5" />
      </span>
      <div>
        <h2 id="asset-pack-picker-title" class="text-base font-semibold text-stone-950 dark:text-zinc-100">
          {$t.collections.assetPackDialogTitle}
        </h2>
        <p id="asset-pack-picker-subtitle" class="text-xs text-stone-500 dark:text-zinc-500">
          {$t.collections.assetPackDialogSubtitle}
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

  {#if !collections.length}
    <p class="my-8 rounded-lg border border-dashed border-stone-300 px-4 py-8 text-center text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-500">
      {$t.collections.noAssetPacks}
    </p>
  {:else}
    <!-- Collections Pills -->
    <div class="mb-3 flex items-center gap-1.5 overflow-x-auto pb-1 text-xs" role="tablist" aria-label={$t.collections.selectAssetPack}>
      {#each prioritizedCollections as collection (collection.id)}
        {@const isSelected = selectedCollectionId === collection.id}
        <button
          type="button"
          role="tab"
          aria-selected={isSelected}
          class={`control-focus inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 font-medium transition-colors ${
            isSelected
              ? 'bg-emerald-600 text-white shadow-xs'
              : 'border border-stone-200 bg-stone-50 text-stone-700 hover:bg-stone-100 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800'
          }`}
          onclick={() => void selectCollection(collection.id)}
        >
          <span>{collection.name}</span>
          {#if collection.asset_alias}
            <span
              class={`rounded px-1 py-0.2 font-mono text-[10px] ${
                isSelected
                  ? 'bg-white/20 text-white'
                  : 'bg-emerald-500/10 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300'
              }`}
            >
              @{collection.asset_alias}
            </span>
          {/if}
          <span class={`text-[10px] ${isSelected ? 'text-white/80' : 'text-stone-400 dark:text-zinc-500'}`}>
            ({collection.image_count})
          </span>
        </button>
      {/each}
    </div>

    <!-- Toolbar: Selection info & Slot availability -->
    <div class="mb-2 flex flex-wrap items-center justify-between gap-2 border-y border-stone-200/80 py-1.5 text-xs dark:border-zinc-800/80">
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="control-focus rounded px-1.5 py-0.5 text-stone-600 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          onclick={selectAll}
          disabled={!memberImages.length}
        >
          {$t.collections.selectAll}
        </button>
        <span class="text-stone-300 dark:text-zinc-700">|</span>
        <button
          type="button"
          class="control-focus rounded px-1.5 py-0.5 text-stone-600 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          onclick={deselectAll}
          disabled={!memberImages.length}
        >
          {$t.collections.deselectAll}
        </button>
        <span class="font-medium text-stone-700 dark:text-zinc-300">
          {$t.collections.selectedCount(selectedIds.size)}
        </span>
      </div>

      <div>
        {#if availableSlots <= 0}
          <span class="rounded bg-red-500/10 px-2 py-0.5 font-medium text-red-600 dark:text-red-400">
            {$t.collections.slotsFull}
          </span>
        {:else}
          <span class="rounded bg-stone-100 px-2 py-0.5 font-medium text-stone-600 dark:bg-zinc-800 dark:text-zinc-400">
            {$t.collections.slotsAvailable(availableSlots)}
          </span>
        {/if}
      </div>
    </div>

    {#if skippedUnsupported > 0}
      <div class="mb-2 rounded-lg border border-amber-300/80 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200">
        {$t.collections.unsupportedImagesNotice(skippedUnsupported)}
      </div>
    {/if}

    <!-- Image Grid -->
    <div class="min-h-0 flex-1 overflow-y-auto pr-1">
      {#if loadingMembers}
        <p class="py-12 text-center text-xs text-stone-500 dark:text-zinc-500">
          {$t.gallery.loading}
        </p>
      {:else if !memberImages.length}
        <p class="py-12 text-center text-xs text-stone-500 dark:text-zinc-500">
          {$t.collections.emptyMembers}
        </p>
      {:else}
        <div class="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4">
          {#each memberImages as img, index (img.id)}
            {@const checked = selectedIds.has(img.id)}
            <button
              type="button"
              class={`group relative flex flex-col overflow-hidden rounded-lg border text-left transition-all ${
                checked
                  ? 'border-emerald-500 bg-emerald-500/5 shadow-xs ring-2 ring-emerald-500/20'
                  : 'border-stone-200 bg-white hover:border-stone-300 dark:border-zinc-800 dark:bg-zinc-950/60 dark:hover:border-zinc-700'
              }`}
              onclick={() => toggleSelect(img.id)}
            >
              <div class="relative aspect-square w-full overflow-hidden bg-stone-100 dark:bg-zinc-900">
                <img
                  src={img.thumbnail_url || img.image_url}
                  alt=""
                  class="h-full w-full object-cover transition-transform group-hover:scale-105"
                  loading="lazy"
                />
                <!-- Checkbox badge -->
                <span
                  class={`absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded border transition-colors ${
                    checked
                      ? 'border-emerald-500 bg-emerald-600 text-white'
                      : 'border-stone-300/90 bg-white/90 text-transparent dark:border-zinc-600 dark:bg-zinc-900/90'
                  }`}
                  aria-hidden="true"
                >
                  <Check class="h-3.5 w-3.5" strokeWidth={3} />
                </span>
                <!-- Position badge -->
                <span class="absolute right-1.5 top-1.5 rounded bg-black/60 px-1 py-0.2 font-mono text-[10px] font-medium text-white shadow-xs backdrop-blur-xs">
                  #{index + 1}
                </span>
              </div>
              <div class="min-w-0 p-1.5">
                <p class="truncate text-xs font-medium text-stone-800 dark:text-zinc-200" title={img.prompt || img.filename}>
                  {img.prompt || img.filename}
                </p>
                <p class="truncate font-mono text-[10px] text-stone-400 dark:text-zinc-500">
                  {img.id}
                </p>
              </div>
            </button>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Actions footer -->
    <div class="mt-3 flex items-center justify-end gap-2 border-t border-stone-200/80 pt-3 dark:border-zinc-800/80">
      <button
        type="button"
        class="control-focus rounded-lg border border-stone-300 px-4 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        onclick={onClose}
      >
        {$t.confirm.cancel}
      </button>
      <button
        type="button"
        class="control-focus inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-xs hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={selectedIds.size === 0 || availableSlots <= 0}
        onclick={handleAdd}
      >
        <Check class="h-3.5 w-3.5" />
        <span>{$t.collections.addSelectedReferences(selectedIds.size)}</span>
      </button>
    </div>
  {/if}
</Overlay>
