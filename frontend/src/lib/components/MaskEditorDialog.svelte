<script lang="ts">
  import { get } from 'svelte/store';
  import { onDestroy, tick } from 'svelte';
  import Brush from 'lucide-svelte/icons/brush';
  import Contrast from 'lucide-svelte/icons/contrast';
  import Eraser from 'lucide-svelte/icons/eraser';
  import Eye from 'lucide-svelte/icons/eye';
  import Layers from 'lucide-svelte/icons/layers';
  import Redo2 from 'lucide-svelte/icons/redo-2';
  import Trash2 from 'lucide-svelte/icons/trash-2';
  import Undo2 from 'lucide-svelte/icons/undo-2';
  import Upload from 'lucide-svelte/icons/upload';
  import X from 'lucide-svelte/icons/x';
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { t } from '$lib/i18n';
  import { dialog } from '$lib/actions/dialog';
  import { confirmStore } from '$lib/stores/confirm';
  import type { EditMask } from '$lib/stores/editSource';
  import {
    MaskImportError,
    createMaskDocument,
    type MaskDocument,
    type MaskPoint,
    type MaskTool
  } from '$lib/features/mask/maskDocument';

  export let open: boolean;
  export let sourceId = '';
  export let imageUrl = '';
  export let label = '';
  export let size = 'auto';
  export let existingMask: EditMask | null = null;
  export let onApply: (mask: EditMask) => void = () => {};
  export let onClose: () => void = () => {};
  export let onError: (message: string) => void = () => {};

  let imageEl: HTMLImageElement | undefined = undefined;
  let overlayEl: HTMLCanvasElement | undefined = undefined;
  let uploadInput: HTMLInputElement | undefined = undefined;

  let doc: MaskDocument | null = null;
  let ready = false;
  let busy = false;
  let drawing = false;
  let tool: MaskTool = 'brush';
  let brushScreen = 32;
  let view: 'overlay' | 'maskOnly' = 'overlay';
  let coverage = 0;
  let dirty = false;
  let paintedAny = false;
  let canUndo = false;
  let canRedo = false;
  let naturalWidth = 0;
  let naturalHeight = 0;
  let cursorVisible = false;
  let cursorX = 0;
  let cursorY = 0;
  let renderHandle: number | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let checkerPattern: CanvasPattern | null = null;
  let tintLayer: HTMLCanvasElement | null = null;
  let prepareToken = 0;

  $: sizeHint = size !== 'auto' && naturalWidth > 0 && size !== `${naturalWidth}x${naturalHeight}`;
  $: if (open && imageUrl) {
    void prepare();
  } else if (!open) {
    teardown();
  }

  function teardown() {
    prepareToken += 1;
    if (renderHandle !== null) {
      cancelAnimationFrame(renderHandle);
      renderHandle = null;
    }
    resizeObserver?.disconnect();
    resizeObserver = null;
    doc?.dispose();
    doc = null;
    ready = false;
    drawing = false;
    checkerPattern = null;
    tintLayer = null;
  }

  onDestroy(teardown);

  function loadImageElement(url: string): Promise<HTMLImageElement> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('image load failed'));
      image.src = url;
    });
  }

  async function prepare() {
    const token = ++prepareToken;
    if (renderHandle !== null) {
      cancelAnimationFrame(renderHandle);
      renderHandle = null;
    }
    resizeObserver?.disconnect();
    resizeObserver = null;
    doc?.dispose();
    doc = null;
    checkerPattern = null;
    ready = false;
    drawing = false;
    dirty = false;
    paintedAny = false;
    canUndo = false;
    canRedo = false;
    coverage = 0;
    naturalWidth = 0;
    naturalHeight = 0;
    busy = true;

    try {
      const image = await loadImageElement(imageUrl);
      if (token !== prepareToken || !open) return;
      naturalWidth = image.naturalWidth || image.width;
      naturalHeight = image.naturalHeight || image.height;
      if (!naturalWidth || !naturalHeight) {
        onError(get(t).maskEditor.importFailed);
        return;
      }
      doc = createMaskDocument(naturalWidth, naturalHeight);
      if (existingMask && existingMask.sourceId === sourceId) {
        try {
          await doc.importFromPng(existingMask.blob);
          if (token !== prepareToken || !open) return;
          coverage = doc.coverage();
          dirty = false;
        } catch {
          // A stale or unreadable stored mask simply starts from a blank canvas.
        }
      }
      ready = true;
    } catch {
      if (token === prepareToken) onError(get(t).maskEditor.importFailed);
    } finally {
      if (token === prepareToken) busy = false;
    }

    await tick();
    if (token !== prepareToken || !open || !imageEl) return;
    resizeObserver = new ResizeObserver(() => {
      resizeOverlay();
      scheduleRender();
    });
    resizeObserver.observe(imageEl);
    resizeOverlay();
    scheduleRender();
  }

  function resizeOverlay() {
    if (!overlayEl || !imageEl) return;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(imageEl.clientWidth * dpr));
    const height = Math.max(1, Math.round(imageEl.clientHeight * dpr));
    if (overlayEl.width !== width || overlayEl.height !== height) {
      overlayEl.width = width;
      overlayEl.height = height;
      // The checkerboard tile is sized in backing pixels.
      checkerPattern = null;
    }
  }

  function scheduleRender() {
    if (renderHandle !== null) return;
    renderHandle = requestAnimationFrame(() => {
      renderHandle = null;
      render();
    });
  }

  function ensureCheckerPattern(context: CanvasRenderingContext2D) {
    if (checkerPattern) return;
    // The pattern lives in backing pixels, so scale the tile by the device
    // pixel ratio to keep 8 CSS px squares readable on HiDPI displays.
    const dpr = Math.max(1, Math.round(window.devicePixelRatio || 1));
    const cell = 8 * dpr;
    const tile = document.createElement('canvas');
    tile.width = cell * 2;
    tile.height = cell * 2;
    const tileContext = tile.getContext('2d');
    if (!tileContext) return;
    tileContext.fillStyle = '#3f3f46';
    tileContext.fillRect(0, 0, cell * 2, cell * 2);
    tileContext.fillStyle = '#71717a';
    tileContext.fillRect(0, 0, cell, cell);
    tileContext.fillRect(cell, cell, cell, cell);
    checkerPattern = context.createPattern(tile, 'repeat');
  }

  function ensureTintLayer(width: number, height: number) {
    if (!tintLayer) tintLayer = document.createElement('canvas');
    if (tintLayer.width !== width || tintLayer.height !== height) {
      tintLayer.width = width;
      tintLayer.height = height;
    }
    return tintLayer;
  }

  function render() {
    if (!overlayEl || !doc) return;
    const width = overlayEl.width;
    const height = overlayEl.height;
    if (!width || !height) return;
    const context = overlayEl.getContext('2d');
    if (!context) return;

    context.globalCompositeOperation = 'source-over';
    context.globalAlpha = 1;
    context.clearRect(0, 0, width, height);

    if (view === 'maskOnly') {
      ensureCheckerPattern(context);
      context.fillStyle = checkerPattern ?? '#27272a';
      context.fillRect(0, 0, width, height);
    }

    // Tint on its own layer: compositing `source-in` directly on the overlay
    // would recolor the mask-only checkerboard along with the marks.
    const layer = ensureTintLayer(width, height);
    const layerContext = layer.getContext('2d');
    if (!layerContext) return;
    layerContext.globalCompositeOperation = 'source-over';
    layerContext.globalAlpha = 1;
    layerContext.clearRect(0, 0, width, height);
    layerContext.drawImage(doc.marks, 0, 0, width, height);
    layerContext.globalCompositeOperation = 'source-in';
    layerContext.fillStyle = 'rgba(16, 185, 129, 0.45)';
    layerContext.fillRect(0, 0, width, height);

    context.drawImage(layer, 0, 0);
  }

  function setView(next: 'overlay' | 'maskOnly') {
    if (view === next) return;
    view = next;
    scheduleRender();
  }

  function overlayRect() {
    return overlayEl?.getBoundingClientRect() ?? null;
  }

  function toImagePoint(event: { clientX: number; clientY: number }): MaskPoint | null {
    const rect = overlayRect();
    if (!rect || !doc || rect.width === 0 || rect.height === 0) return null;
    return {
      x: Math.min(doc.width, Math.max(0, ((event.clientX - rect.left) / rect.width) * doc.width)),
      y: Math.min(doc.height, Math.max(0, ((event.clientY - rect.top) / rect.height) * doc.height))
    };
  }

  function brushImageSize() {
    const rect = overlayRect();
    if (!rect || !doc || rect.width === 0) return 1;
    return Math.max(1, (brushScreen / rect.width) * doc.width);
  }

  function updateCursor(event: PointerEvent) {
    const rect = overlayRect();
    if (!rect) return;
    cursorX = event.clientX - rect.left;
    cursorY = event.clientY - rect.top;
    cursorVisible = true;
  }

  function updateHistoryFlags() {
    canUndo = Boolean(doc?.canUndo());
    canRedo = Boolean(doc?.canRedo());
  }

  function handlePointerDown(event: PointerEvent) {
    if (!doc || !overlayEl || busy || event.button !== 0) return;
    const point = toImagePoint(event);
    if (!point) return;
    overlayEl.setPointerCapture(event.pointerId);
    drawing = true;
    doc.beginStroke(tool, brushImageSize(), point);
    paintedAny = true;
    dirty = true;
    updateHistoryFlags();
    updateCursor(event);
    scheduleRender();
  }

  function handlePointerMove(event: PointerEvent) {
    if (!doc) return;
    updateCursor(event);
    if (!drawing) return;
    const coalesced =
      typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [event];
    const points = coalesced
      .map((candidate) => toImagePoint(candidate))
      .filter((point): point is MaskPoint => point !== null);
    if (!points.length) return;
    doc.extendStroke(points);
    scheduleRender();
  }

  function handlePointerUp(event: PointerEvent) {
    if (!doc || !drawing) return;
    drawing = false;
    if (overlayEl?.hasPointerCapture(event.pointerId)) {
      overlayEl.releasePointerCapture(event.pointerId);
    }
    coverage = doc.endStroke();
    updateHistoryFlags();
    scheduleRender();
  }

  function setBrushSize(value: number) {
    brushScreen = Math.min(160, Math.max(4, Math.round(value)));
  }

  function undo() {
    if (!doc || !doc.canUndo()) return;
    coverage = doc.undo() ?? 0;
    dirty = true;
    updateHistoryFlags();
    scheduleRender();
  }

  function redo() {
    if (!doc || !doc.canRedo()) return;
    coverage = doc.redo() ?? 0;
    dirty = true;
    updateHistoryFlags();
    scheduleRender();
  }

  function clearMask() {
    if (!doc) return;
    coverage = doc.clear();
    dirty = true;
    paintedAny = false;
    updateHistoryFlags();
    scheduleRender();
  }

  function invertMask() {
    if (!doc) return;
    coverage = doc.invert();
    dirty = true;
    updateHistoryFlags();
    scheduleRender();
  }

  function maskImportMessage(error: unknown) {
    const messages = get(t).messages;
    if (error instanceof MaskImportError) {
      if (error.code === 'not-png') return messages.editMaskUploadNotPng;
      if (error.code === 'no-alpha') return messages.editMaskUploadNoAlpha;
      if (error.code === 'too-large') return messages.editMaskUploadTooLarge;
      if (error.code === 'size') {
        return messages.editMaskUploadSizeMismatch(
          error.width,
          error.height,
          naturalWidth,
          naturalHeight
        );
      }
    }
    return get(t).maskEditor.importFailed;
  }

  async function handleMaskUpload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || !doc) return;
    busy = true;
    try {
      coverage = await doc.importFromPng(file);
      dirty = true;
      updateHistoryFlags();
      scheduleRender();
    } catch (error) {
      onError(maskImportMessage(error));
    } finally {
      busy = false;
    }
  }

  async function applyMask() {
    if (!doc || busy || coverage <= 0) return;
    busy = true;
    try {
      const result = await doc.exportPng();
      if (result.coverage <= 0) {
        onError(get(t).maskEditor.noAreaMarked);
        return;
      }
      dirty = false;
      onApply({
        sourceId,
        blob: result.blob,
        width: doc.width,
        height: doc.height,
        coverage: result.coverage,
        previewUrl: URL.createObjectURL(result.blob),
        origin: paintedAny ? 'painted' : 'uploaded'
      });
    } catch {
      onError(get(t).maskEditor.importFailed);
    } finally {
      busy = false;
    }
  }

  async function requestClose() {
    if (dirty) {
      const confirmed = await confirmStore.confirm({
        title: get(t).confirm.unsavedChangesTitle,
        message: get(t).confirm.unsavedChangesMessage,
        confirmLabel: get(t).common.discard,
        cancelLabel: get(t).confirm.cancel,
        closeLabel: get(t).confirm.closeLabel,
        variant: 'danger'
      });
      if (!confirmed) return;
    }
    onClose();
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!open) return;
    const meta = event.metaKey || event.ctrlKey;
    const key = event.key.toLowerCase();
    if (meta && key === 'z') {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
      return;
    }
    if (meta && key === 'y') {
      event.preventDefault();
      redo();
      return;
    }
    if (meta) return;
    const target = event.target as HTMLElement | null;
    if (target?.closest('textarea, input[type="text"]')) return;
    if (key === 'b') tool = 'brush';
    else if (key === 'e') tool = 'eraser';
    else if (event.key === '[') {
      event.preventDefault();
      setBrushSize(brushScreen - 8);
    } else if (event.key === ']') {
      event.preventDefault();
      setBrushSize(brushScreen + 8);
    }
  }
</script>

<svelte:window on:keydown={handleKeydown} />

{#if open}
  <div
    class="mobile-dialog-root fixed inset-0 z-[75] flex items-center justify-center bg-black/75 p-4"
    in:overlayIn
    out:overlayOut
  >
    <button
      class="absolute inset-0"
      type="button"
      tabindex="-1"
      aria-label={$t.maskEditor.closeLabel}
      on:click={requestClose}
    ></button>
    <div
      class="mobile-dvh-dialog overlay-panel relative flex max-h-[calc(100vh-32px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950"
      in:dialogIn
      out:dialogOut
      aria-labelledby="mask-editor-title"
      use:dialog={{ open, onClose: requestClose }}
    >
      <div class="flex items-start justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div class="min-w-0">
          <h2 id="mask-editor-title" class="truncate text-sm font-semibold text-zinc-100">
            {$t.maskEditor.title(label)}
          </h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.maskEditor.keyboardHint}</p>
        </div>
        <button
          type="button"
          class="mobile-touch-target control-focus flex items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
          aria-label={$t.maskEditor.closeLabel}
          title={$t.maskEditor.closeLabel}
          on:click={requestClose}
        >
          <X size={16} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>

      {#if sizeHint}
        <p class="border-b border-zinc-800/70 bg-zinc-900/60 px-4 py-2 text-xs text-zinc-400">
          {$t.maskEditor.sizeHint(size)}
        </p>
      {/if}

      <div class="relative flex min-h-0 flex-1 items-center justify-center bg-zinc-950 p-3">
        {#if busy}
          <div
            class="absolute inset-x-0 top-0 z-10 flex items-center justify-center gap-2 border-b border-zinc-800 bg-zinc-900/90 px-3 py-1.5 text-xs text-zinc-300"
            role="status"
          >
            <span class="spinner" aria-hidden="true"></span>
            {$t.maskEditor.busy}
          </div>
        {/if}
        <div class="relative inline-block max-h-full max-w-full">
          <img
            bind:this={imageEl}
            src={imageUrl}
            alt={label}
            class="block max-h-[52vh] max-w-full select-none rounded-lg object-contain sm:max-h-[58vh]"
            draggable="false"
          />
          {#if ready}
            <canvas
              bind:this={overlayEl}
              class="absolute inset-0 h-full w-full cursor-none touch-none rounded-lg"
              aria-label={$t.maskEditor.canvasLabel}
              on:pointerdown={handlePointerDown}
              on:pointermove={handlePointerMove}
              on:pointerup={handlePointerUp}
              on:pointercancel={handlePointerUp}
              on:pointerleave={() => (cursorVisible = false)}
              on:contextmenu|preventDefault
            ></canvas>
            {#if cursorVisible}
              <span
                class="pointer-events-none absolute rounded-full border border-emerald-400/80 bg-emerald-400/10"
                style={`left:${cursorX}px;top:${cursorY}px;width:${brushScreen}px;height:${brushScreen}px;transform:translate(-50%,-50%)`}
                aria-hidden="true"
              ></span>
            {/if}
          {/if}
        </div>
      </div>

      <div class="space-y-2.5 border-t border-zinc-800 px-3 py-3">
        <div class="flex flex-wrap items-center gap-2">
          <div class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1" role="group">
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={tool === 'brush'}
              class:text-white={tool === 'brush'}
              class:text-zinc-300={tool !== 'brush'}
              class:hover:bg-zinc-800={tool !== 'brush'}
              aria-pressed={tool === 'brush'}
              aria-label={$t.maskEditor.brush}
              title={$t.maskEditor.brush}
              on:click={() => (tool = 'brush')}
            >
              <Brush size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.brush}</span>
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={tool === 'eraser'}
              class:text-white={tool === 'eraser'}
              class:text-zinc-300={tool !== 'eraser'}
              class:hover:bg-zinc-800={tool !== 'eraser'}
              aria-pressed={tool === 'eraser'}
              aria-label={$t.maskEditor.eraser}
              title={$t.maskEditor.eraser}
              on:click={() => (tool = 'eraser')}
            >
              <Eraser size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.eraser}</span>
            </button>
          </div>

          <label class="flex min-w-[168px] flex-1 items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5">
            <input
              type="range"
              min="4"
              max="160"
              step="2"
              class="control-focus h-1.5 w-full accent-emerald-600"
              aria-label={$t.maskEditor.brushSize(brushScreen)}
              bind:value={brushScreen}
            />
            <span class="w-[86px] shrink-0 text-xs tabular-nums text-zinc-400">
              {$t.maskEditor.brushSize(brushScreen)}
            </span>
          </label>

          <div class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1" role="group">
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!canUndo}
              aria-label={$t.maskEditor.undo}
              title={$t.maskEditor.undo}
              on:click={undo}
            >
              <Undo2 size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!canRedo}
              aria-label={$t.maskEditor.redo}
              title={$t.maskEditor.redo}
              on:click={redo}
            >
              <Redo2 size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-zinc-800"
              aria-label={$t.maskEditor.invert}
              title={$t.maskEditor.invert}
              on:click={invertMask}
            >
              <Contrast size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800"
              aria-label={$t.maskEditor.clear}
              title={$t.maskEditor.clear}
              on:click={clearMask}
            >
              <Trash2 size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>

          <div class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1" role="group">
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-zinc-800={view === 'overlay'}
              class:text-zinc-100={view === 'overlay'}
              class:text-zinc-400={view !== 'overlay'}
              aria-pressed={view === 'overlay'}
              title={$t.maskEditor.viewOverlay}
              aria-label={$t.maskEditor.viewOverlay}
              on:click={() => setView('overlay')}
            >
              <Layers size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.viewOverlay}</span>
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-zinc-800={view === 'maskOnly'}
              class:text-zinc-100={view === 'maskOnly'}
              class:text-zinc-400={view !== 'maskOnly'}
              aria-pressed={view === 'maskOnly'}
              title={$t.maskEditor.viewMaskOnly}
              aria-label={$t.maskEditor.viewMaskOnly}
              on:click={() => setView('maskOnly')}
            >
              <Eye size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.viewMaskOnly}</span>
            </button>
          </div>

          <input
            bind:this={uploadInput}
            type="file"
            accept="image/png"
            class="hidden"
            aria-label={$t.maskEditor.uploadMask}
            on:change={handleMaskUpload}
          />
          <button
            type="button"
            class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
            aria-label={$t.maskEditor.uploadMask}
            title={$t.maskEditor.uploadMaskHint}
            on:click={() => uploadInput?.click()}
          >
            <Upload size={15} strokeWidth={1.9} aria-hidden="true" />
            <span>{$t.maskEditor.uploadMask}</span>
          </button>
        </div>

        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-xs tabular-nums text-zinc-400" aria-live="polite">
            {$t.maskEditor.coverage((coverage * 100).toFixed(1))}
          </p>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-lg px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
              on:click={requestClose}
            >
              {$t.maskEditor.cancel}
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || coverage <= 0}
              title={coverage <= 0 ? $t.maskEditor.noAreaMarked : $t.maskEditor.apply}
              on:click={applyMask}
            >
              {$t.maskEditor.apply}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
{/if}
