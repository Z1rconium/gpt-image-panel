<script lang="ts">
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import type { AssistantGalleryMetadataResponse } from '$lib/api/types/assistant';
  import type { GalleryEntry } from '$lib/api/types/gallery';
  import { t } from '$lib/i18n';
  import { lightboxStore } from '$lib/stores/lightbox';
  import {
    displayImageSize,
    downloadUrl,
    formatLocalTime,
    imageUrl,
    thumbnailUrl
  } from '$lib/utils/format';
  import { dialog } from '$lib/actions/dialog';

  interface Props {
    aiAssistantEnabled?: boolean;
    aiMetadata?: AssistantGalleryMetadataResponse | null;
    aiLoadingImageId?: string | null;
    canNavigatePrevious?: boolean;
    canNavigateNext?: boolean;
    navigating?: boolean;
    onClose?: () => void;
    onEdit?: (image: GalleryEntry) => void;
    onFavorite?: (image: GalleryEntry) => void;
    onDelete?: (image: GalleryEntry) => void;
    onCopyPrompt?: (image: GalleryEntry) => void;
    onCopyUrl?: (image: GalleryEntry) => void;
    onUsePrompt?: (image: GalleryEntry) => void;
    onUseAll?: (image: GalleryEntry) => void;
    onAiDescribe?: (image: GalleryEntry) => void;
    onAiAnalyze?: (image: GalleryEntry) => void;
    onNavigatePrevious?: () => void;
    onNavigateNext?: () => void;
  }

  let {
    aiAssistantEnabled = false,
    aiMetadata = null,
    aiLoadingImageId = null,
    canNavigatePrevious = false,
    canNavigateNext = false,
    navigating = false,
    onClose = () => {},
    onEdit = () => {},
    onFavorite = () => {},
    onDelete = () => {},
    onCopyPrompt = () => {},
    onCopyUrl = () => {},
    onUsePrompt = () => {},
    onUseAll = () => {},
    onAiDescribe = () => {},
    onAiAnalyze = () => {},
    onNavigatePrevious = () => {},
    onNavigateNext = () => {}
  }: Props = $props();

  const SWIPE_MIN_DISTANCE = 52;
  const SWIPE_MAX_DURATION_MS = 800;
  const SWIPE_AXIS_RATIO = 1.25;
  const SWIPE_MAX_DRAG = 96;
  const SWIPE_IGNORE_SELECTOR = 'a, button, input, select, textarea, [role="button"], [data-swipe-ignore]';

  const image = $derived($lightboxStore.image);
  const open = $derived(Boolean(image));

  let swipePointerId: number | null = null;
  let swipeStartX = 0;
  let swipeStartY = 0;
  let swipeStartTime = 0;
  let dragging = $state(false);
  let dragOffsetX = $state(0);
  let loadedImageSrc = $state('');
  let failedImageSrc = $state('');
  // Fit-to-window keeps object-fit parity with the gallery; actual size renders
  // the original at 1:1 inside a scroll viewport.
  let zoomActual = $state(false);
  let zoomViewport: HTMLDivElement | null = $state(null);
  let panPointerId: number | null = null;
  let panStartX = 0;
  let panStartY = 0;
  let panStartScrollLeft = 0;
  let panStartScrollTop = 0;

  const aiLoading = $derived(Boolean(image && aiLoadingImageId === image.id));
  const fullImageSrc = $derived(image ? imageUrl(image.filename, image.image_url) : '');
  const previewImageSrc = $derived(
    image?.thumbnail_status === 'ready' ? thumbnailUrl(image.filename, image.thumbnail_url) : ''
  );
  const fullImageLoaded = $derived(Boolean(fullImageSrc && loadedImageSrc === fullImageSrc));
  const fullImageFailed = $derived(Boolean(fullImageSrc && failedImageSrc === fullImageSrc));
  const stageTransform = $derived(!zoomActual && dragOffsetX ? `translateX(${dragOffsetX}px)` : '');

  // A new original always opens fit-to-window.
  $effect(() => {
    if (fullImageSrc) {
      zoomActual = false;
    }
  });

  function setZoomActual(next: boolean) {
    zoomActual = next;
    // Deferred a frame so the viewport is scrollable again before it is rewound.
    if (next) requestAnimationFrame(() => zoomViewport?.scrollTo({ top: 0, left: 0 }));
  }

  function handleFullImageLoad() {
    loadedImageSrc = fullImageSrc;
    failedImageSrc = '';
  }

  function handleFullImageError() {
    failedImageSrc = fullImageSrc;
  }

  function resetSwipeTracking() {
    swipePointerId = null;
    swipeStartX = 0;
    swipeStartY = 0;
    swipeStartTime = 0;
  }

  // Ends live 1:1 tracking now, then lets the stylesheet's transition carry
  // the offset back to rest - deferred a frame so the browser sees the
  // transition re-enabled before the value changes, or it won't animate.
  function snapBack() {
    dragging = false;
    requestAnimationFrame(() => {
      dragOffsetX = 0;
    });
  }

  function canStartSwipe(event: PointerEvent) {
    const target = event.target;
    return (
      event.isPrimary &&
      event.pointerType !== 'mouse' &&
      !navigating &&
      (canNavigatePrevious || canNavigateNext) &&
      !(target instanceof Element && target.closest(SWIPE_IGNORE_SELECTOR))
    );
  }

  // Mouse drag pans the 1:1 original through scroll offsets, so it composes with
  // native wheel, trackpad, and touch scrolling instead of fighting it.
  function canStartPan(event: PointerEvent) {
    const target = event.target;
    return (
      event.isPrimary &&
      event.pointerType === 'mouse' &&
      !(target instanceof Element && target.closest(SWIPE_IGNORE_SELECTOR))
    );
  }

  function handleSwipePointerDown(event: PointerEvent) {
    if (zoomActual) {
      if (!canStartPan(event) || !zoomViewport) return;
      panPointerId = event.pointerId;
      panStartX = event.clientX;
      panStartY = event.clientY;
      panStartScrollLeft = zoomViewport.scrollLeft;
      panStartScrollTop = zoomViewport.scrollTop;
      dragging = true;
      (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
      return;
    }
    if (!canStartSwipe(event)) return;
    swipePointerId = event.pointerId;
    swipeStartX = event.clientX;
    swipeStartY = event.clientY;
    swipeStartTime = Date.now();
    dragging = true;
    dragOffsetX = 0;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function handleSwipePointerMove(event: PointerEvent) {
    if (panPointerId === event.pointerId) {
      if (!zoomViewport) return;
      zoomViewport.scrollLeft = panStartScrollLeft - (event.clientX - panStartX);
      zoomViewport.scrollTop = panStartScrollTop - (event.clientY - panStartY);
      return;
    }
    if (swipePointerId !== event.pointerId) return;
    const dx = event.clientX - swipeStartX;
    const dy = event.clientY - swipeStartY;
    if (Math.abs(dy) > Math.abs(dx) * SWIPE_AXIS_RATIO) return;
    const towardPrevious = dx > 0;
    const canFollow = towardPrevious ? canNavigatePrevious : canNavigateNext;
    // Resist past an edge with no image to swipe to, like a boundary.
    const resisted = canFollow ? dx : dx * 0.3;
    dragOffsetX = Math.max(-SWIPE_MAX_DRAG, Math.min(SWIPE_MAX_DRAG, resisted));
  }

  function releasePointer(event: PointerEvent) {
    const el = event.currentTarget as HTMLElement | null;
    if (el?.hasPointerCapture?.(event.pointerId)) {
      try {
        el.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may have already been released
      }
    }
  }

  function handleSwipePointerUp(event: PointerEvent) {
    if (panPointerId === event.pointerId) {
      panPointerId = null;
      dragging = false;
      releasePointer(event);
      return;
    }
    if (swipePointerId !== event.pointerId) return;

    const dx = event.clientX - swipeStartX;
    const dy = event.clientY - swipeStartY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);
    const elapsed = Date.now() - swipeStartTime;
    const horizontalSwipe = absX >= SWIPE_MIN_DISTANCE && absX > absY * SWIPE_AXIS_RATIO && elapsed <= SWIPE_MAX_DURATION_MS;

    if (horizontalSwipe && dx < 0 && canNavigateNext) onNavigateNext();
    else if (horizontalSwipe && dx > 0 && canNavigatePrevious) onNavigatePrevious();

    releasePointer(event);
    resetSwipeTracking();
    snapBack();
  }

  function handleSwipePointerCancel(event: PointerEvent) {
    if (panPointerId === event.pointerId) {
      panPointerId = null;
      dragging = false;
      releasePointer(event);
      return;
    }
    if (swipePointerId !== event.pointerId) return;
    releasePointer(event);
    resetSwipeTracking();
    snapBack();
  }
</script>

{#if open && image}
  <div class="mobile-lightbox-root fixed inset-0 z-[70] flex items-center justify-center bg-black/75 p-4" in:overlayIn out:overlayOut>
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.lightbox.closeLabel} onclick={onClose}></button>
    <div
      class="lightbox-shell relative" in:dialogIn out:dialogOut
      aria-labelledby="lightbox-title"
      use:dialog={{ open, onClose }}
    >
      <div
        class="lightbox-media"
        role="group"
        aria-label={$t.lightbox.title}
        onpointerdown={handleSwipePointerDown}
        onpointermove={handleSwipePointerMove}
        onpointerup={handleSwipePointerUp}
        onpointercancel={handleSwipePointerCancel}
      >
        {#if fullImageLoaded}
          <div class="lightbox-zoom-controls">
            <button
              type="button"
              class="mobile-touch-target control-focus"
              aria-label={$t.lightbox.zoomFit}
              title={$t.lightbox.zoomFit}
              aria-pressed={!zoomActual}
              onclick={() => setZoomActual(false)}
            >
              Fit
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus"
              aria-label={$t.lightbox.zoomActual}
              title={$t.lightbox.zoomActual}
              aria-pressed={zoomActual}
              onclick={() => setZoomActual(true)}
            >
              1:1
            </button>
          </div>
        {/if}
        <div class="flex h-full min-h-0 w-full flex-col">
          <div
            class="flex min-h-0 flex-1"
            class:lightbox-image-viewport-zoomed={zoomActual}
            bind:this={zoomViewport}
          >
            {#key fullImageSrc}
              <div
                class="lightbox-image-stage"
                class:lightbox-image-dragging={dragging}
                class:lightbox-image-zoomed={zoomActual}
                style:transform={stageTransform || null}
              >
                {#if previewImageSrc}
                  <img
                    src={previewImageSrc}
                    alt=""
                    aria-hidden="true"
                    class:lightbox-preview-hidden={fullImageLoaded}
                    class="lightbox-preview-img"
                    decoding="async"
                  />
                {/if}
                {#if !fullImageLoaded}
                  <div class:lightbox-load-failed={fullImageFailed} class="lightbox-load-status" role="status">
                    {#if !fullImageFailed}
                      <span class="lightbox-load-track" aria-hidden="true"><span></span></span>
                    {/if}
                    <span>{fullImageFailed ? $t.lightbox.originalLoadFailed : $t.lightbox.loadingOriginal}</span>
                  </div>
                {/if}
                <img
                  src={fullImageSrc}
                  alt={image.prompt}
                  class:lightbox-img-loaded={fullImageLoaded}
                  class="lightbox-img"
                  decoding="async"
                  draggable="false"
                  fetchpriority="high"
                  width={image.image_width || undefined}
                  height={image.image_height || undefined}
                  onload={handleFullImageLoad}
                  onerror={handleFullImageError}
                />
              </div>
            {/key}
          </div>
          <div class="mt-4 flex h-10 shrink-0 items-center justify-between">
            {#if canNavigatePrevious}
              <button
                type="button"
                class="mobile-touch-target control-focus inline-flex h-10 w-10 items-center justify-center rounded-lg border border-stone-300 text-lg leading-none text-stone-700 transition-colors hover:bg-stone-100 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                aria-label={$t.lightbox.previousImage}
                disabled={navigating}
                onclick={onNavigatePrevious}
              >
                <span aria-hidden="true">&larr;</span>
              </button>
            {:else}
              <span class="h-10 w-10" aria-hidden="true"></span>
            {/if}

            {#if canNavigateNext}
              <button
                type="button"
                class="mobile-touch-target control-focus inline-flex h-10 w-10 items-center justify-center rounded-lg border border-stone-300 text-lg leading-none text-stone-700 transition-colors hover:bg-stone-100 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                aria-label={$t.lightbox.nextImage}
                disabled={navigating}
                onclick={onNavigateNext}
              >
                <span aria-hidden="true">&rarr;</span>
              </button>
            {:else}
              <span class="h-10 w-10" aria-hidden="true"></span>
            {/if}
          </div>
        </div>
      </div>
      <aside class="lightbox-details flex min-h-0 flex-col">
        <div class="flex items-start justify-between gap-3 border-b border-stone-200 p-5 dark:border-zinc-800">
          <div class="min-w-0">
            <h2 id="lightbox-title" class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.lightbox.title}</h2>
            <p class="mt-1 truncate text-xs text-stone-500 dark:text-zinc-500">{image.filename}</p>
          </div>
          <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.lightbox.closeLabel} onclick={onClose}>x</button>
        </div>
        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          <div>
            <div class="mb-1 text-xs font-medium text-stone-500 dark:text-zinc-500">{$t.common.prompt}</div>
            <p class="whitespace-pre-wrap text-sm text-stone-800 dark:text-zinc-200">{image.prompt}</p>
          </div>
          {#if aiAssistantEnabled}
            <section class="rounded-lg border border-stone-200 bg-stone-50/80 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="mb-3 flex items-center justify-between gap-3">
                <h3 class="text-xs font-semibold text-stone-700 dark:text-zinc-300">{$t.aiAssistant.title}</h3>
                {#if aiLoading}
                  <span class="text-[11px] text-zinc-500">{$t.aiAssistant.working}</span>
                {/if}
              </div>
              <div class="grid grid-cols-2 gap-2">
                <button type="button" disabled={aiLoading} class="control-focus rounded-lg border border-zinc-700 px-2 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50" onclick={() => onAiDescribe(image)}>
                  {$t.lightbox.aiDescribe}
                </button>
                <button type="button" disabled={aiLoading} class="control-focus rounded-lg border border-cyan-500/35 px-2 py-2 text-xs text-cyan-700 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-cyan-200" onclick={() => onAiAnalyze(image)}>
                  {$t.lightbox.aiAnalyze}
                </button>
              </div>
              {#if aiMetadata && aiMetadata.image_id === image.id && (aiMetadata.description || aiMetadata.prompt)}
                <div class="mt-3 space-y-3 text-xs leading-5 text-stone-700 dark:text-zinc-300">
                  {#if aiMetadata.description}
                    <div>
                      <div class="mb-1 font-semibold text-stone-500 dark:text-zinc-500">{$t.lightbox.aiDescription}</div>
                      <p class="whitespace-pre-wrap">{aiMetadata.description}</p>
                    </div>
                  {/if}
                  {#if aiMetadata.prompt}
                    <div>
                      <div class="mb-1 font-semibold text-stone-500 dark:text-zinc-500">{$t.lightbox.aiPromptResult}</div>
                      <p class="whitespace-pre-wrap">{aiMetadata.prompt}</p>
                    </div>
                  {/if}
                </div>
              {/if}
            </section>
          {/if}
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.size}</div>
              <div class="mt-1 text-stone-700 dark:text-zinc-300">{displayImageSize(image)}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.model}</div>
              <div class="mt-1 truncate text-stone-700 dark:text-zinc-300">{image.model || '-'}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.completedAt}</div>
              <div class="mt-1 whitespace-nowrap text-stone-700 dark:text-zinc-300">{formatLocalTime(image.completed_at)}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.preset}</div>
              <div class="mt-1 truncate text-stone-700 dark:text-zinc-300">{image.api_preset_name || '-'}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.duration}</div>
              <div class="mt-1 text-stone-700 dark:text-zinc-300">{image.duration || '-'}</div>
            </div>
          </div>
        </div>
        <div class="lightbox-details-actions grid grid-cols-2 gap-2 border-t border-stone-200 p-5 dark:border-zinc-800">
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={() => onEdit(image)}>{$t.common.edit}</button>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={() => onFavorite(image)}>{image.favorite ? $t.common.unfavorite : $t.common.favorite}</button>
          <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200" onclick={() => onUsePrompt(image)}>{$t.common.usePrompt}</button>
          <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200" onclick={() => onUseAll(image)}>{$t.common.useAllParams}</button>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={() => onCopyPrompt(image)}>{$t.common.copyPrompt}</button>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" onclick={() => onCopyUrl(image)}>{$t.common.copyUrl}</button>
          <a href={downloadUrl(image.filename)} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-center text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">{$t.common.download}</a>
          <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10" onclick={() => onDelete(image)}>{$t.common.delete}</button>
        </div>
      </aside>
    </div>
  </div>
{/if}
