<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { GalleryEntry, GalleryResponse } from '$lib/api/types/gallery';
  import GalleryFilterToolbar from '$lib/components/gallery/GalleryFilterToolbar.svelte';
  import GalleryPagination from '$lib/components/gallery/GalleryPagination.svelte';
  import { t } from '$lib/i18n';
  import { galleryActivityStore, galleryStore, type GalleryFilters } from '$lib/stores/gallery';
  import { uiStore } from '$lib/stores/ui';
  import CloudUpload from 'lucide-svelte/icons/cloud-upload';
  import Download from 'lucide-svelte/icons/download';
  import FileText from 'lucide-svelte/icons/file-text';
  import Pencil from 'lucide-svelte/icons/pencil';
  import SlidersHorizontal from 'lucide-svelte/icons/sliders-horizontal';
  import Star from 'lucide-svelte/icons/star';
  import Trash2 from 'lucide-svelte/icons/trash-2';
  import X from 'lucide-svelte/icons/x';
  import { displayImageSize, formatBytes, thumbnailUrl } from '$lib/utils/format';

  interface Props {
    canSyncR2?: boolean;
    canNodeImageUpload?: boolean;
    canAiAnalyze?: boolean;
    onFilter?: (key: keyof GalleryFilters, value: string | boolean) => void;
    onResetFilters?: () => void;
    onPage?: (page: number, direction?: 'next' | 'prev' | 'jump') => void;
    onLoadStats?: () => void;
    onFavorite?: (image: GalleryEntry) => void;
    onDelete?: (image: GalleryEntry) => void;
    onDeleteAll?: () => void;
    onImport?: (file: File) => void;
    onExport?: () => void;
    onSync?: () => void;
    onOpen?: (image: GalleryEntry) => void;
    onEdit?: (image: GalleryEntry) => void;
    onUsePrompt?: (image: GalleryEntry) => void;
    onUseAll?: (image: GalleryEntry) => void;
    onNodeImageUpload?: (image: GalleryEntry) => void;
    onBatchFavorite?: (favorite: boolean) => void;
    onBatchDelete?: () => void;
    onBatchNodeImageUpload?: () => void;
    onBatchAiAnalyze?: () => void;
  }

  let {
    canSyncR2 = false,
    canNodeImageUpload = false,
    canAiAnalyze = false,
    onFilter = () => {},
    onResetFilters = () => {},
    onPage = () => {},
    onLoadStats = () => {},
    onFavorite = () => {},
    onDelete = () => {},
    onDeleteAll = () => {},
    onImport = () => {},
    onExport = () => {},
    onSync = () => {},
    onOpen = () => {},
    onEdit = () => {},
    onUsePrompt = () => {},
    onUseAll = () => {},
    onNodeImageUpload = () => {},
    onBatchFavorite = () => {},
    onBatchDelete = () => {},
    onBatchNodeImageUpload = () => {},
    onBatchAiAnalyze = () => {}
  }: Props = $props();

  const skeletonCards = Array.from({ length: 6 });
  // The first row is visible on load, so it is fetched eagerly; only the very
  // first thumbnail is marked high priority. Everything after the first row is
  // lazy with the browser's default priority.
  const EAGER_THUMB_COUNT = 3;
  const THUMBNAIL_PLACEHOLDER_SRC = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';

  function thumbnailLoading(index: number): 'eager' | 'lazy' {
    return index < EAGER_THUMB_COUNT ? 'eager' : 'lazy';
  }

  function thumbnailFetchPriority(index: number): 'high' | 'auto' {
    return index === 0 ? 'high' : 'auto';
  }

  let importInput: HTMLInputElement | null = $state(null);
  let failedThumbnailUrls = $state(new Map<string, string>());
  let poppedFavoriteId = $state('');
  let favoritePopTimer: ReturnType<typeof setTimeout> | undefined;

  const gallery = $derived($galleryStore.gallery);
  const filters = $derived($galleryStore.filters);
  const loading = $derived($galleryStore.loading);
  const operationStatus = $derived($galleryActivityStore.operationStatus);
  const selectionMode = $derived($galleryStore.selectionMode);
  const selectedIds = $derived($galleryStore.selectedIds);
  const selectionTokenCount = $derived($galleryStore.selectionToken?.count || 0);

  // One beat, on the way in only: turning a favourite off is not a celebration.
  function popFavorite(image: GalleryEntry) {
    if (image.favorite) return;
    clearTimeout(favoritePopTimer);
    poppedFavoriteId = '';
    requestAnimationFrame(() => {
      poppedFavoriteId = image.id;
    });
    favoritePopTimer = setTimeout(() => {
      poppedFavoriteId = '';
    }, 280);
  }

  onDestroy(() => clearTimeout(favoritePopTimer));

  const images = $derived(gallery?.images || []);
  $effect(() => {
    pruneFailedThumbnailUrls(images);
  });
  const currentPage = $derived(gallery?.page || 1);
  const totalPages = $derived(Math.max(gallery?.total_pages || 1, 1));
  const initialLoading = $derived(loading && images.length === 0);
  const busy = $derived(loading || Boolean(operationStatus));
  const selectedAllFiltered = $derived(selectionTokenCount > 0);
  const selectedCount = $derived(selectedAllFiltered ? selectionTokenCount : selectedIds.size);
  const pageSelectedCount = $derived(selectedAllFiltered ? images.length : images.filter((image) => selectedIds.has(image.id)).length);
  const hasSelection = $derived(selectedCount > 0);
  const selectionSummary = $derived(
    selectedAllFiltered
      ? $t.gallery.filteredSelection(selectedCount)
      : selectedCount > pageSelectedCount
        ? $t.gallery.crossPageSelection(pageSelectedCount, selectedCount)
        : $t.gallery.pageSelection(selectedCount)
  );
  const hasFilters = $derived(
    Boolean(
      filters.prompt.trim() ||
        filters.model ||
        filters.preset ||
        filters.size ||
        filters.dateFrom ||
        filters.dateTo ||
        filters.favorite
    )
  );

  function importSelected() {
    const file = importInput?.files?.[0];
    if (file) onImport(file);
    if (importInput) importInput.value = '';
  }

  function handleImageClick(image: GalleryEntry) {
    if (selectionMode) {
      galleryStore.toggleSelection(image);
      return;
    }
    onOpen(image);
  }

  function handleGalleryAction(event: MouseEvent, action: () => void) {
    event.preventDefault();
    event.stopPropagation();
    action();
  }

  function galleryImageSrc(image: GalleryEntry) {
    if (!thumbnailReady(image)) return THUMBNAIL_PLACEHOLDER_SRC;
    const src = thumbnailUrl(image.filename, image.thumbnail_url);
    return failedThumbnailUrls.get(image.id) === src ? THUMBNAIL_PLACEHOLDER_SRC : src;
  }

  function pruneFailedThumbnailUrls(visibleImages: GalleryEntry[]) {
    const visibleIds = new Set(visibleImages.map((image) => image.id));
    if ([...failedThumbnailUrls.keys()].every((imageId) => visibleIds.has(imageId))) return;
    failedThumbnailUrls = new Map(
      [...failedThumbnailUrls].filter(([imageId]) => visibleIds.has(imageId))
    );
  }

  function thumbnailReady(image: GalleryEntry) {
    return !image.thumbnail_status || image.thumbnail_status === 'ready';
  }

  function handleThumbnailLoad(image: GalleryEntry) {
    if (!thumbnailReady(image)) return;
    if (!failedThumbnailUrls.has(image.id)) return;
    failedThumbnailUrls = new Map(failedThumbnailUrls);
    failedThumbnailUrls.delete(image.id);
  }

  function handleThumbnailError(image: GalleryEntry) {
    if (!thumbnailReady(image)) return;
    failedThumbnailUrls = new Map(failedThumbnailUrls).set(image.id, thumbnailUrl(image.filename, image.thumbnail_url));
  }

  function isImageSelected(image: GalleryEntry) {
    return selectedAllFiltered || selectedIds.has(image.id);
  }
</script>

<section class="app-section px-1 py-1 sm:px-0">
  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <div>
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.gallery.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">
        {gallery?.total ? $t.gallery.imageCount(gallery.total) : $t.gallery.noImages}
        {#if gallery?.total_bytes}
          <span class="ml-2">{formatBytes(gallery.total_bytes)}</span>
        {:else if gallery?.total}
          <button type="button" class="control-focus ml-2 rounded text-xs font-medium text-stone-600 hover:text-stone-900 dark:text-zinc-400 dark:hover:text-zinc-200" onclick={onLoadStats}>
            {$t.gallery.showSize}
          </button>
        {/if}
      </p>
    </div>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={() => galleryStore.setSelectionMode(!selectionMode)}>
        {selectionMode ? $t.gallery.cancelSelection : $t.gallery.select}
      </button>
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={busy} onclick={() => importInput?.click()}>
        {operationStatus?.kind === 'import' ? $t.gallery.importing : $t.gallery.import}
      </button>
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={busy} onclick={onExport}>
        {operationStatus?.kind === 'export' ? $t.gallery.exporting : $t.gallery.exportZip}
      </button>
      <button
        type="button"
        class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        disabled={busy || !canSyncR2}
        title={canSyncR2 ? $t.gallery.syncR2 : $t.messages.r2BackupUnavailable}
        onclick={onSync}
      >
        {operationStatus?.kind === 'sync' ? $t.gallery.syncing : $t.gallery.syncR2}
      </button>
      <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-700 hover:bg-red-500/10 dark:text-red-300" onclick={onDeleteAll}>
        {$t.gallery.deleteAll}
      </button>
      <input bind:this={importInput} type="file" accept=".zip,application/zip" class="hidden" onchange={importSelected} />
    </div>
  </div>

  <GalleryFilterToolbar {gallery} {filters} {onFilter} onReset={onResetFilters} />

  {#if selectionMode}
    <div class="mb-4 flex flex-col gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="text-xs font-medium text-emerald-800 dark:text-emerald-200">{selectionSummary}</div>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={galleryStore.selectPage}>{$t.gallery.selectAllPage}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!gallery?.total || busy} onclick={galleryStore.selectFiltered}>{$t.gallery.selectFiltered}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection} onclick={galleryStore.clearSelection}>{$t.gallery.clearSelection}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} onclick={() => galleryStore.batchDownload(uiStore.showToast)}>{operationStatus?.kind === 'download' ? $t.gallery.downloading : $t.gallery.downloadSelected}</button>
        {#if canNodeImageUpload}
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} onclick={onBatchNodeImageUpload}>
            {operationStatus?.kind === 'nodeimage_upload' ? $t.gallery.uploadingToNodeImage : $t.gallery.uploadSelectedToNodeImage}
          </button>
        {/if}
        {#if canAiAnalyze}
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} onclick={onBatchAiAnalyze}>{operationStatus?.kind === 'ai_analyze' ? $t.gallery.aiAnalyzing : $t.gallery.aiAnalyzeSelected}</button>
        {/if}
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} onclick={() => onBatchFavorite(true)}>{$t.gallery.favoriteSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} onclick={() => onBatchFavorite(false)}>{$t.gallery.unfavoriteSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-red-500/40 px-2.5 py-2 text-xs text-red-700 hover:bg-red-500/10 disabled:opacity-40 dark:text-red-300" disabled={!hasSelection || busy} onclick={onBatchDelete}>{$t.gallery.deleteSelected}</button>
      </div>
    </div>
  {/if}

  {#if operationStatus}
    <div class="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3" role="status" aria-live="polite">
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="text-xs font-semibold text-emerald-950 dark:text-emerald-100">{operationStatus.label}</p>
          <p class="mt-1 text-xs text-emerald-800 dark:text-emerald-200/80">{operationStatus.detail}</p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <div class="text-xs text-emerald-800 dark:text-emerald-200">{operationStatus.progress === null ? $t.gallery.notInterruptible : `${operationStatus.progress}%`}</div>
          {#if operationStatus.cancel}
            <button
              type="button"
              class="control-focus inline-flex min-h-9 items-center gap-1.5 rounded-md border border-emerald-700/40 px-2.5 py-1.5 text-xs font-medium text-emerald-900 hover:bg-emerald-500/10 disabled:cursor-wait disabled:opacity-50 dark:border-emerald-300/40 dark:text-emerald-100 dark:hover:bg-emerald-300/10"
              disabled={operationStatus.cancelPending}
              onclick={() => void operationStatus.cancel?.()}
            >
              <X class="h-3.5 w-3.5" aria-hidden="true" />
              {$t.gallery.cancelOperation}
            </button>
          {/if}
        </div>
      </div>
      <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-emerald-950/20 dark:bg-emerald-950/70">
        {#if operationStatus.progress === null}
          <div class="progress-indeterminate h-full w-1/3 rounded-full bg-emerald-500 dark:bg-emerald-300"></div>
        {:else}
          <div class="h-full rounded-full bg-emerald-500 transition-[width] dark:bg-emerald-300" style={`width: ${Number(operationStatus.progress) || 0}%`}></div>
        {/if}
      </div>
    </div>
  {/if}

  {#if initialLoading}
    <div class="grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4" aria-label={$t.gallery.loading}>
      {#each skeletonCards as _}
        <div class="overflow-hidden rounded-xl border border-stone-200 bg-stone-100/90 dark:border-zinc-800 dark:bg-zinc-950/45">
          <div class="aspect-square animate-pulse bg-stone-200/80 dark:bg-zinc-800/60"></div>
          <div class="space-y-3 p-3">
            <div class="h-4 w-5/6 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/70"></div>
            <div class="h-3 w-1/2 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/60"></div>
            <div class="flex gap-2">
              <div class="h-7 w-14 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/60"></div>
              <div class="h-7 w-16 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/60"></div>
            </div>
          </div>
        </div>
      {/each}
    </div>
  {:else if images.length === 0}
    <div class="rounded-xl border border-dashed border-stone-300 bg-stone-100/80 px-4 py-10 text-center dark:border-zinc-800 dark:bg-zinc-950/35">
      <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{hasFilters ? $t.gallery.noMatch : $t.gallery.empty}</p>
      <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{hasFilters ? $t.gallery.noMatchHint : $t.gallery.emptyHint}</p>
    </div>
  {:else}
    <div class="relative" aria-busy={loading}>
      {#if loading}
        <div class="gallery-loading-overlay pointer-events-none absolute inset-0 z-10 rounded-xl">
          <div class="absolute right-3 top-3 rounded-lg border border-stone-300 bg-white/90 px-3 py-2 text-xs text-stone-700 shadow-lg dark:border-zinc-700 dark:bg-zinc-950/90 dark:text-zinc-300">
            {$t.gallery.loading}
          </div>
        </div>
      {/if}

      <div class={`gallery-grid grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4 ${loading ? 'opacity-70' : ''}`}>
        {#each images as image, index (image.id)}
          <article class={`gallery-card overflow-hidden rounded-xl border ${isImageSelected(image) ? 'border-emerald-400 bg-emerald-500/10' : 'border-stone-200 bg-white/85 dark:border-zinc-800 dark:bg-zinc-950/45'}`}>
            <button
              type="button"
              class="gallery-media-well control-focus relative block aspect-square w-full bg-stone-100 dark:bg-zinc-950"
              aria-label={image.prompt}
              aria-pressed={selectionMode ? isImageSelected(image) : undefined}
              onclick={() => handleImageClick(image)}
            >
              {#if selectionMode}
                <span class="absolute left-2 top-2 z-10 rounded-md bg-white/90 px-2 py-1 text-xs font-medium text-stone-800 dark:bg-zinc-950/80 dark:text-zinc-100">
                  {isImageSelected(image) ? '✓' : ''}
                </span>
                <span class="sr-only">{isImageSelected(image) ? $t.gallery.selectedCount(1) : $t.gallery.select}</span>
              {/if}
              <picture class="block h-full w-full">
                <img
                  src={galleryImageSrc(image)}
                  alt={image.prompt}
                  class="gallery-image preview-empty h-full w-full object-cover"
                  loading={thumbnailLoading(index)}
                  fetchpriority={thumbnailFetchPriority(index)}
                  decoding="async"
                  width={image.image_width || undefined}
                  height={image.image_height || undefined}
                  onload={() => handleThumbnailLoad(image)}
                  onerror={() => handleThumbnailError(image)}
                />
              </picture>
            </button>
            <div class="space-y-2 p-2.5">
              <div class="min-w-0">
                <p class="line-clamp-2 text-xs leading-5 text-stone-800 dark:text-zinc-200">{image.prompt}</p>
                <p class="mt-1 truncate text-xs leading-4 text-stone-500 dark:text-zinc-500">{displayImageSize(image)} / {image.model || '-'}</p>
              </div>
              <div class="gallery-card-actions grid grid-cols-4 gap-1">
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200"
                  aria-label={$t.common.usePrompt}
                  title={$t.common.usePrompt}
                  onclick={(event) => handleGalleryAction(event, () => onUsePrompt(image))}
                >
                  <FileText aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200"
                  aria-label={$t.common.useAllParams}
                  title={$t.common.useAllParams}
                  onclick={(event) => handleGalleryAction(event, () => onUseAll(image))}
                >
                  <SlidersHorizontal aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-pressed={image.favorite}
                  aria-label={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  title={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  onclick={(event) =>
                    handleGalleryAction(event, () => {
                      popFavorite(image);
                      onFavorite(image);
                    })}
                >
                  <Star aria-hidden="true" class={poppedFavoriteId === image.id ? 'favorite-pop' : ''} />
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.common.edit}
                  title={$t.common.edit}
                  onclick={(event) => handleGalleryAction(event, () => onEdit(image))}
                >
                  <Pencil aria-hidden="true" />
                </button>
                <a
                  href={`/api/download/${encodeURIComponent(image.filename)}`}
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.common.download}
                  title={$t.common.download}
                  onclick={(event) => event.stopPropagation()}
                >
                  <Download aria-hidden="true" />
                </a>
                {#if canNodeImageUpload}
                  <button
                    type="button"
                    class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    disabled={busy}
                    aria-label={$t.gallery.uploadToNodeImage}
                    title={$t.gallery.uploadToNodeImage}
                    onclick={(event) => handleGalleryAction(event, () => onNodeImageUpload(image))}
                  >
                    <CloudUpload aria-hidden="true" />
                  </button>
                {/if}
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300"
                  aria-label={$t.common.delete}
                  title={$t.common.delete}
                  onclick={(event) => handleGalleryAction(event, () => onDelete(image))}
                >
                  <Trash2 aria-hidden="true" />
                </button>
              </div>
            </div>
          </article>
        {/each}
      </div>
    </div>

    <GalleryPagination {currentPage} {totalPages} hasPrevious={Boolean(gallery?.has_prev)} hasNext={Boolean(gallery?.has_next)} {loading} {onPage} />
  {/if}
</section>
