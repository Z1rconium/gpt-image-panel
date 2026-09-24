<script lang="ts">
  import { get } from 'svelte/store';
  import { onDestroy, tick } from 'svelte';
  import Brush from 'lucide-svelte/icons/brush';
  import Circle from 'lucide-svelte/icons/circle';
  import CircleHelp from 'lucide-svelte/icons/circle-help';
  import Contrast from 'lucide-svelte/icons/contrast';
  import Eraser from 'lucide-svelte/icons/eraser';
  import Eye from 'lucide-svelte/icons/eye';
  import Hand from 'lucide-svelte/icons/hand';
  import Layers from 'lucide-svelte/icons/layers';
  import Lasso from 'lucide-svelte/icons/lasso';
  import Magnet from 'lucide-svelte/icons/magnet';
  import Maximize from 'lucide-svelte/icons/maximize';
  import Minus from 'lucide-svelte/icons/minus';
  import Plus from 'lucide-svelte/icons/plus';
  import Redo2 from 'lucide-svelte/icons/redo-2';
  import SlidersHorizontal from 'lucide-svelte/icons/sliders-horizontal';
  import Sparkles from 'lucide-svelte/icons/sparkles';
  import Square from 'lucide-svelte/icons/square';
  import Trash2 from 'lucide-svelte/icons/trash-2';
  import Undo2 from 'lucide-svelte/icons/undo-2';
  import Upload from 'lucide-svelte/icons/upload';
  import X from 'lucide-svelte/icons/x';
  import ZoomIn from 'lucide-svelte/icons/zoom-in';
  import ZoomOut from 'lucide-svelte/icons/zoom-out';
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { t } from '$lib/i18n';
  import { dialog } from '$lib/actions/dialog';
  import { confirmStore } from '$lib/stores/confirm';
  import type { EditMask } from '$lib/stores/editSource';
  import {
    MAX_SMOOTH_PX,
    MaskImportError,
    createMaskDocument,
    type MaskDocument,
    type MaskImportMode,
    type MaskPoint,
    type MaskShapeMode,
    type MaskTool
  } from '$lib/features/mask/maskDocument';
  import {
    SNAP_RADIUS_DEFAULT,
    SNAP_RADIUS_MAX,
    SNAP_RADIUS_MIN,
    computeEdgeMap,
    edgeMapSize,
    snapToEdge,
    type EdgeMap
  } from '$lib/features/mask/maskSnap';
  import {
    REGION_EDGE_RANGE_MAX,
    REGION_EDGE_RANGE_MIN,
    REGION_GROW_MAX,
    REGION_STRENGTH_DEFAULT,
    regionParamsFor,
    type MaskRegionPreferences,
    type MaskRegionStrength
  } from '$lib/features/mask/maskRegion';
  import { MaskRegionClient } from '$lib/features/mask/maskRegionClient';

  export let open: boolean;
  export let sourceId = '';
  export let imageUrl = '';
  export let label = '';
  export let size = 'auto';
  export let existingMask: EditMask | null = null;
  export let onApply: (mask: EditMask) => void = () => {};
  export let onRemove: () => void = () => {};
  export let onClose: () => void = () => {};
  export let onError: (message: string) => void = () => {};
  export let onNotice: (message: string) => void = () => {};

  let stageEl: HTMLDivElement | undefined = undefined;
  let overlayEl: HTMLCanvasElement | undefined = undefined;
  let cursorEl: HTMLSpanElement | undefined = undefined;
  let uploadInput: HTMLInputElement | undefined = undefined;

  let doc: MaskDocument | null = null;
  let ready = false;
  let busy = false;
  let drawing = false;
  let tool: MaskTool = 'brush';
  let shapeMode: MaskShapeMode = 'add';
  let brushScreen = 32;
  let smoothPx = 0;
  let view: 'overlay' | 'maskOnly' = 'overlay';
  let coverage = 0;
  let dirty = false;
  let paintedAny = false;
  let uploadedAny = false;
  let canUndo = false;
  let canRedo = false;
  let naturalWidth = 0;
  let naturalHeight = 0;
  let cursorVisible = false;
  let renderHandle: number | null = null;
  // Set on every brush/eraser pointermove and drained inside the same rAF that
  // repaints the canvas. Coverage is O(1) now, so this only batches the Svelte
  // re-render, not the pixel work.
  let liveCoverageDirty = false;
  let resizeObserver: ResizeObserver | null = null;
  let checkerPattern: CanvasPattern | null = null;
  let prepareToken = 0;
  let showHelp = false;
  let smoothTimer: ReturnType<typeof setTimeout> | null = null;

  // Magnetic edge snapping (lasso + rectangle). Off by default; the choice and
  // radius are remembered across sessions. The edge map is a downscaled Sobel
  // gradient of the primary image, built lazily the first time it is needed.
  let snapEnabled = false;
  let snapRadiusScreen = SNAP_RADIUS_DEFAULT;
  let snapMap: EdgeMap | null = null;
  let naturalImage: HTMLImageElement | null = null;
  // Continuity anchor for path snapping: the point the previous event snapped
  // to, so a contour does not flip between two parallel edges (U3).
  let lastSnapPoint: MaskPoint | null = null;

  // Region gap filling ("区域处理"): closes pinholes and seams, pulls the
  // selection to the picture frame, fills enclosed holes, and optionally
  // follows object edges. On by default — the gaps it repairs are exactly the
  // ones that are invisible in the preview but leave artifacts upstream.
  let regionPrefs: MaskRegionPreferences = {
    enabled: true,
    strength: REGION_STRENGTH_DEFAULT,
    edgeSnap: false,
    edgeRange: null,
    grow: 0
  };
  let showRegionPanel = false;
  let regionPending = false;
  let autoCoverage = 0;
  let autoHuge = false;
  let regionClient: MaskRegionClient | null = null;
  let regionTimer: ReturnType<typeof setTimeout> | null = null;
  let autoCanvas: HTMLCanvasElement | null = null;
  let autoPattern: CanvasPattern | null = null;

  const MIN_ZOOM = 0.1;
  const MAX_ZOOM = 8;
  const SNAP_STORAGE_KEY = 'maskEditor.snap';
  const REGION_STORAGE_KEY = 'maskEditor.region';
  const SNAP_TOOLS: MaskTool[] = ['rect', 'ellipse', 'lasso'];
  const REGION_STRENGTHS: MaskRegionStrength[] = ['light', 'normal', 'strong'];
  let viewScale = 1;
  let viewX = 0;
  let viewY = 0;
  let panning = false;
  let spacePressed = false;
  let pointerOverCanvas = false;
  let panLastX = 0;
  let panLastY = 0;
  let lastBrushEnd: MaskPoint | null = null;

  $: sizeHint = size !== 'auto' && naturalWidth > 0 && size !== `${naturalWidth}x${naturalHeight}`;
  $: hasExistingMask = Boolean(existingMask && existingMask.sourceId === sourceId);
  $: snapAvailable = SNAP_TOOLS.includes(tool);
  $: strengthOptions = [
    { value: 'light' as MaskRegionStrength, label: $t.maskEditor.strengthLight },
    { value: 'normal' as MaskRegionStrength, label: $t.maskEditor.strengthNormal },
    { value: 'strong' as MaskRegionStrength, label: $t.maskEditor.strengthStrong }
  ];
  $: brushRingSize = brushScreen / viewScale;
  $: cursorHidden = !cursorVisible || spacePressed || (tool !== 'brush' && tool !== 'eraser');
  $: canvasCursorClass = spacePressed
    ? panning
      ? 'cursor-grabbing'
      : 'cursor-grab'
    : tool === 'pan'
      ? panning
        ? 'cursor-grabbing'
        : 'cursor-grab'
      : tool === 'brush' || tool === 'eraser'
        ? 'cursor-none'
        : 'cursor-crosshair';
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
    if (smoothTimer !== null) {
      clearTimeout(smoothTimer);
      smoothTimer = null;
    }
    if (regionTimer !== null) {
      clearTimeout(regionTimer);
      regionTimer = null;
    }
    regionClient?.teardown();
    regionClient = null;
    resizeObserver?.disconnect();
    resizeObserver = null;
    doc?.dispose();
    doc = null;
    ready = false;
    drawing = false;
    checkerPattern = null;
    autoCanvas = null;
    autoPattern = null;
    cursorVisible = false;
    snapMap = null;
    naturalImage = null;
    lastSnapPoint = null;
    lastBrushEnd = null;
    regionPending = false;
    autoCoverage = 0;
    autoHuge = false;
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
    if (smoothTimer !== null) {
      clearTimeout(smoothTimer);
      smoothTimer = null;
    }
    if (regionTimer !== null) {
      clearTimeout(regionTimer);
      regionTimer = null;
    }
    resizeObserver?.disconnect();
    resizeObserver = null;
    doc?.dispose();
    doc = null;
    checkerPattern = null;
    autoCanvas = null;
    autoPattern = null;
    ready = false;
    drawing = false;
    dirty = false;
    paintedAny = false;
    uploadedAny = false;
    canUndo = false;
    canRedo = false;
    coverage = 0;
    autoCoverage = 0;
    autoHuge = false;
    regionPending = false;
    naturalWidth = 0;
    naturalHeight = 0;
    busy = true;
    smoothPx = 0;
    shapeMode = 'add';
    showHelp = false;
    showRegionPanel = false;
    viewScale = 1;
    viewX = 0;
    viewY = 0;
    snapMap = null;
    naturalImage = null;
    lastSnapPoint = null;
    lastBrushEnd = null;
    restoreSnapPreference();
    restoreRegionPreference();

    // U2: reopening a mask this session saved replaces the localStorage habit
    // above with the exact parameters it was drawn with; one without saved
    // state (a stale save, or one restored from a task) is only available as
    // an already-processed PNG, so the non-idempotent options are forced off
    // for this session instead of stacking on a selection that already went
    // through them.
    const reopeningMask = Boolean(existingMask && existingMask.sourceId === sourceId);
    const reopeningState = reopeningMask ? (existingMask?.editorState ?? null) : null;
    if (reopeningState) {
      regionPrefs = reopeningState.region;
      smoothPx = reopeningState.smoothPx;
    } else if (reopeningMask) {
      regionPrefs = { ...regionPrefs, edgeSnap: false, grow: 0 };
    }

    try {
      const image = await loadImageElement(imageUrl);
      if (token !== prepareToken || !open) return;
      naturalWidth = image.naturalWidth || image.width;
      naturalHeight = image.naturalHeight || image.height;
      if (!naturalWidth || !naturalHeight) {
        onError(get(t).maskEditor.importFailed);
        return;
      }
      naturalImage = image;
      doc = createMaskDocument(naturalWidth, naturalHeight);
      doc.setRegionParams(resolvedRegionParams());
      if (reopeningMask && existingMask) {
        try {
          if (reopeningState) {
            coverage = await doc.restoreSnapshot(
              reopeningState.marks,
              reopeningState.protected,
              reopeningState.smoothPx
            );
          } else {
            // A stored mask without saved editor state is an already-processed
            // PNG, so the import is the only description of it we have.
            await doc.importFromPng(existingMask.blob);
            coverage = doc.syncProcessed();
          }
          if (token !== prepareToken || !open) return;
          dirty = false;
        } catch {
          // A stale or unreadable stored mask simply starts from a blank canvas.
        }
      }
      ready = true;
      autoCoverage = doc.autoCoverage();
      scheduleRegion(0);
    } catch {
      if (token === prepareToken) onError(get(t).maskEditor.importFailed);
    } finally {
      if (token === prepareToken) busy = false;
    }

    await tick();
    if (token !== prepareToken || !open || !stageEl) return;
    resizeObserver = new ResizeObserver(() => {
      clampView();
      resizeOverlay();
      scheduleRender();
    });
    resizeObserver.observe(stageEl);
    resizeOverlay();
    fitView();
    scheduleRender();
    // A snap preference restored from a previous session needs its edge map
    // before the first stroke, otherwise the first drag would go unsnapped.
    if (snapEnabled) void ensureSnapMap();
  }

  function resizeOverlay() {
    if (!overlayEl || !stageEl) return;
    // The overlay covers the stage viewport (not the image), so a 4096px
    // photo at 2x DPR never allocates two 8192px canvases; the render pass
    // scales image coordinates into this backing store instead.
    const rect = stageEl.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (overlayEl.width !== width || overlayEl.height !== height) {
      overlayEl.width = width;
      overlayEl.height = height;
      // Both tiles are sized in backing pixels.
      checkerPattern = null;
      autoPattern = null;
    }
  }

  function scheduleRender() {
    if (renderHandle !== null) return;
    renderHandle = requestAnimationFrame(() => {
      renderHandle = null;
      if (liveCoverageDirty) {
        liveCoverageDirty = false;
        publishCoverage();
      }
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
    tileContext.fillRect(0, 0, cell * 2, tile.height);
    tileContext.fillStyle = '#71717a';
    tileContext.fillRect(0, 0, cell, cell);
    tileContext.fillRect(cell, cell, cell, cell);
    checkerPattern = context.createPattern(tile, 'repeat');
  }

  function render() {
    if (!overlayEl || !doc) return;
    const width = overlayEl.width;
    const height = overlayEl.height;
    if (!width || !height) return;
    const context = overlayEl.getContext('2d');
    if (!context) return;
    const dpr = window.devicePixelRatio || 1;

    context.setTransform(1, 0, 0, 1, 0, 0);
    context.globalCompositeOperation = 'source-over';
    context.globalAlpha = 1;
    context.clearRect(0, 0, width, height);

    if (view === 'maskOnly') {
      ensureCheckerPattern(context);
      context.fillStyle = checkerPattern ?? '#27272a';
      context.fillRect(0, 0, width, height);
    }

    // Draw the processed (edge-smoothed, re-binarized) region whenever it is
    // up to date so the preview matches the coverage readout and the export;
    // raw marks keep mid-stroke feedback instant. Marks are already emerald
    // in the document layer, so they are drawn straight into the view
    // transform — and only the slice that lands on screen, so the browser
    // never has to touch off-screen pixels of a 4096² mask.
    context.setTransform(viewScale * dpr, 0, 0, viewScale * dpr, viewX * dpr, viewY * dpr);
    context.globalAlpha = 0.45;
    const base = doc.processedFresh() ? doc.processed : doc.marks;
    const visible = visibleSourceRect(doc.width, doc.height, width / dpr, height / dpr);
    if (visible) {
      context.drawImage(
        base,
        visible.x,
        visible.y,
        visible.width,
        visible.height,
        visible.x,
        visible.y,
        visible.width,
        visible.height
      );
    }
    context.globalAlpha = 1;
    drawAutoLayer(context, width, height, dpr);
    doc.drawActiveShapePreview(context);
    context.setTransform(1, 0, 0, 1, 0, 0);
  }

  /**
   * Draw the automatic additions (D) on top of the painted area.
   *
   * It goes through a viewport-sized scratch canvas so the stripe pattern can
   * be applied with `source-in` in screen space: the layer reads as the same
   * emerald signal color as the marks, but textured, so the two are told apart
   * without color alone (DESIGN.md).
   */
  function drawAutoLayer(
    context: CanvasRenderingContext2D,
    width: number,
    height: number,
    dpr: number
  ) {
    const layer = doc?.autoLayer;
    if (!layer || !width || !height) return;
    if (!autoCanvas) autoCanvas = document.createElement('canvas');
    if (autoCanvas.width !== width || autoCanvas.height !== height) {
      autoCanvas.width = width;
      autoCanvas.height = height;
      autoPattern = null;
    }
    const layerContext = autoCanvas.getContext('2d');
    if (!layerContext) return;
    ensureAutoPattern(context);

    layerContext.setTransform(1, 0, 0, 1, 0, 0);
    layerContext.globalCompositeOperation = 'source-over';
    layerContext.globalAlpha = 1;
    layerContext.clearRect(0, 0, width, height);
    layerContext.setTransform(viewScale * dpr, 0, 0, viewScale * dpr, viewX * dpr, viewY * dpr);
    layerContext.drawImage(
      layer.source,
      layer.bbox.x,
      layer.bbox.y,
      layer.bbox.width,
      layer.bbox.height
    );
    layerContext.setTransform(1, 0, 0, 1, 0, 0);
    if (autoPattern) {
      layerContext.globalCompositeOperation = 'source-in';
      layerContext.fillStyle = autoPattern;
      layerContext.fillRect(0, 0, width, height);
    }
    layerContext.globalCompositeOperation = 'source-over';

    context.setTransform(1, 0, 0, 1, 0, 0);
    context.drawImage(autoCanvas, 0, 0);
    context.setTransform(viewScale * dpr, 0, 0, viewScale * dpr, viewX * dpr, viewY * dpr);
  }

  /** 45° hatch over the emerald wash, in backing pixels. */
  function ensureAutoPattern(context: CanvasRenderingContext2D) {
    if (autoPattern) return;
    const dpr = Math.max(1, Math.round(window.devicePixelRatio || 1));
    const cell = 6 * dpr;
    const tile = document.createElement('canvas');
    tile.width = cell * 2;
    tile.height = cell * 2;
    const tileContext = tile.getContext('2d');
    if (!tileContext) return;
    tileContext.fillStyle = 'rgba(16, 185, 129, 0.25)';
    tileContext.fillRect(0, 0, tile.width, tile.height);
    tileContext.strokeStyle = 'rgba(16, 185, 129, 0.6)';
    tileContext.lineWidth = Math.max(1, 2 * dpr);
    tileContext.beginPath();
    tileContext.moveTo(-cell * 0.5, cell * 2.5);
    tileContext.lineTo(cell * 2.5, -cell * 0.5);
    tileContext.stroke();
    autoPattern = context.createPattern(tile, 'repeat');
  }

  function clampSource(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
  }

  /** Image-space rectangle currently on screen; null when it is off-canvas. */
  function visibleSourceRect(
    imageWidth: number,
    imageHeight: number,
    stageWidth: number,
    stageHeight: number
  ) {
    if (viewScale <= 0) return null;
    const left = clampSource(-viewX / viewScale, 0, imageWidth);
    const top = clampSource(-viewY / viewScale, 0, imageHeight);
    const right = clampSource((stageWidth - viewX) / viewScale, 0, imageWidth);
    const bottom = clampSource((stageHeight - viewY) / viewScale, 0, imageHeight);
    if (right <= left || bottom <= top) return null;
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  /**
   * Publish the coverage readout. The document folds each painted segment into
   * its running total, so this is a plain field read — it can run on every
   * frame of a drag instead of once per stroke. Only a change in the displayed
   * tenth of a percent touches reactive state.
   */
  function publishCoverage() {
    if (!doc) return;
    // The headline number is the region the user controls; the automatic
    // additions are reported next to it as a separate delta, so the readout
    // never claims credit for something the pipeline is still computing.
    const next = doc.baseCoverage();
    // Only a change in the displayed tenth of a percent touches reactive
    // state — but leaving zero always does, because a selection far smaller
    // than the readout can show still decides whether saving is allowed.
    if (
      Math.round(next * 1000) !== Math.round(coverage * 1000) ||
      (next > 0) !== (coverage > 0)
    ) {
      coverage = next;
    }
    const auto = doc.autoCoverage();
    if (Math.round(auto * 1000) !== Math.round(autoCoverage * 1000)) autoCoverage = auto;
    updateAutoHint();
  }

  /**
   * Flag an automatic pass that added far more than the user painted — over
   * half of the selection again, or more than 5% of the whole frame. It never
   * blocks the save; it just asks the user to look at the preview once.
   */
  function updateAutoHint() {
    if (!doc) return;
    const total = doc.width * doc.height;
    const added = doc.autoPixelCount;
    autoHuge =
      added > 0 &&
      (added > total * 0.05 || added * 2 > Math.round(doc.baseCoverage() * total));
  }

  /**
   * Snap preference is a per-user editor habit rather than per-image state, so
   * it lives in localStorage and carries across sessions. Defaults to off.
   */
  function restoreSnapPreference() {
    snapEnabled = false;
    snapRadiusScreen = SNAP_RADIUS_DEFAULT;
    if (typeof localStorage === 'undefined') return;
    try {
      const raw = localStorage.getItem(SNAP_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as { enabled?: unknown; radius?: unknown };
      snapEnabled = parsed.enabled === true;
      if (typeof parsed.radius === 'number' && Number.isFinite(parsed.radius)) {
        snapRadiusScreen = Math.min(
          SNAP_RADIUS_MAX,
          Math.max(SNAP_RADIUS_MIN, Math.round(parsed.radius))
        );
      }
    } catch {
      // A corrupt entry just falls back to the defaults.
    }
  }

  function persistSnapPreference() {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(
        SNAP_STORAGE_KEY,
        JSON.stringify({ enabled: snapEnabled, radius: snapRadiusScreen })
      );
    } catch {
      // Storage can be unavailable (private mode); snapping still works.
    }
  }

  /**
   * Region processing is an editor habit like snapping: the choices live in
   * localStorage, defaulting to "on, standard strength" (plan decision 1).
   */
  function restoreRegionPreference() {
    regionPrefs = {
      enabled: true,
      strength: REGION_STRENGTH_DEFAULT,
      edgeSnap: false,
      edgeRange: null,
      grow: 0
    };
    if (typeof localStorage === 'undefined') return;
    try {
      const raw = localStorage.getItem(REGION_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<MaskRegionPreferences>;
      regionPrefs = {
        enabled: parsed.enabled !== false,
        strength: REGION_STRENGTHS.includes(parsed.strength as MaskRegionStrength)
          ? (parsed.strength as MaskRegionStrength)
          : REGION_STRENGTH_DEFAULT,
        edgeSnap: parsed.edgeSnap === true,
        edgeRange:
          typeof parsed.edgeRange === 'number' && Number.isFinite(parsed.edgeRange)
            ? parsed.edgeRange
            : null,
        grow:
          typeof parsed.grow === 'number' && Number.isFinite(parsed.grow)
            ? Math.min(REGION_GROW_MAX, Math.max(0, Math.round(parsed.grow)))
            : 0
      };
    } catch {
      // A corrupt entry just falls back to the defaults.
    }
  }

  function persistRegionPreference() {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(REGION_STORAGE_KEY, JSON.stringify(regionPrefs));
    } catch {
      // Storage can be unavailable (private mode); the pass still runs.
    }
  }

  /** Resolved gap-fill distances for the current document, or null when off. */
  function resolvedRegionParams() {
    if (!doc || !regionPrefs.enabled) return null;
    return regionParamsFor(doc.width, doc.height, regionPrefs);
  }

  /** Push the current parameters into the document (this drops the stale D). */
  function applyRegionParams() {
    if (!doc) return;
    doc.setRegionParams(resolvedRegionParams());
    autoCoverage = doc.autoCoverage();
    autoHuge = false;
    regionPending = doc.regionPending();
  }

  /**
   * Ask for a gap-fill pass once the user stops changing things. Every result
   * carries the version it was computed from, so an answer that arrives after
   * a newer edit is simply dropped.
   */
  function scheduleRegion(delay = 150) {
    if (!doc || !regionPrefs.enabled) return;
    if (regionTimer !== null) clearTimeout(regionTimer);
    regionTimer = setTimeout(() => {
      regionTimer = null;
      void refreshRegion();
    }, delay);
  }

  /**
   * Bring the additions layer up to date, waiting for the newest result.
   *
   * A result that a later request replaced is asked for again rather than
   * accepted: the export has to render exactly what the readout promised, and
   * the worker drops superseded work by design.
   */
  async function refreshRegion() {
    // Pin the document: an in-flight request must not touch a newer one.
    const maskDoc = doc;
    if (!maskDoc) return;
    const client = regionClient ?? (regionClient = new MaskRegionClient());
    regionPending = true;
    try {
      for (let attempt = 0; attempt < 4 && doc === maskDoc; attempt += 1) {
        const request = maskDoc.regionSnapshot(snapMap);
        if (!request) break;
        const outcome = await client.requestRegion(request);
        if (doc !== maskDoc) return;
        if (!outcome.superseded) maskDoc.applyAutoLayer(outcome);
        if (!maskDoc.regionPending()) break;
      }
      publishCoverage();
    } finally {
      if (doc === maskDoc) {
        regionPending = maskDoc.regionPending();
        scheduleRender();
      }
    }
  }

  function setRegionPref(update: Partial<MaskRegionPreferences>) {
    regionPrefs = { ...regionPrefs, ...update };
    persistRegionPreference();
    applyRegionParams();
    if (regionPrefs.enabled) {
      if (regionPrefs.edgeSnap) void ensureSnapMap();
      scheduleRegion(120);
    } else {
      publishCoverage();
      scheduleRender();
    }
  }

  function toggleAutoFill() {
    setRegionPref({ enabled: !regionPrefs.enabled });
  }

  /** Size-derived default for the edge-snap range slider (plan §2.4). */
  function edgeRangeDefault() {
    if (!doc) return 8;
    return regionParamsFor(doc.width, doc.height, { ...regionPrefs, edgeRange: null }).edgeRange;
  }

  /**
   * Downscaled Sobel gradient map of the primary image. Built lazily — a user
   * who never turns snapping on never pays for it.
   */
  function buildSnapMap(): EdgeMap | null {
    if (!naturalImage || !doc) return null;
    const size = edgeMapSize(doc.width, doc.height);
    const canvas = document.createElement('canvas');
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(naturalImage, 0, 0, size.width, size.height);
    const data = context.getImageData(0, 0, size.width, size.height);
    canvas.width = 0;
    canvas.height = 0;
    return computeEdgeMap(data.data, size.width, size.height, {
      scale: size.scale,
      sourceWidth: doc.width,
      sourceHeight: doc.height
    });
  }

  /**
   * Build the edge map in the worker when it can: `createImageBitmap` does the
   * downscale off the main thread and the Sobel pass never touches it. Falls
   * back to the synchronous build above wherever either step is unavailable.
   */
  async function buildSnapMapAsync(): Promise<EdgeMap | null> {
    if (!naturalImage || !doc) return null;
    if (typeof createImageBitmap !== 'function') return null;
    const size = edgeMapSize(doc.width, doc.height);
    let bitmap: ImageBitmap;
    try {
      bitmap = await createImageBitmap(naturalImage, {
        resizeWidth: size.width,
        resizeHeight: size.height
      });
    } catch {
      return null;
    }
    const client = regionClient ?? (regionClient = new MaskRegionClient());
    return client.requestEdgeMap(bitmap, {
      width: size.width,
      height: size.height,
      scale: size.scale,
      sourceWidth: doc.width,
      sourceHeight: doc.height
    });
  }

  async function ensureSnapMap() {
    if (snapMap || !naturalImage || !doc) return;
    busy = true;
    // Let the busy banner paint before anything blocks the thread.
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    if (!doc) {
      busy = false;
      return;
    }
    const document = doc;
    const workerMap = await buildSnapMapAsync();
    busy = false;
    if (doc !== document) return;
    snapMap = workerMap ?? buildSnapMap();
    // A freshly built map is exactly what an edge-snap pass was waiting for.
    if (regionPrefs.enabled && regionPrefs.edgeSnap) scheduleRegion(0);
    scheduleRender();
  }

  async function setSnapEnabled(next: boolean) {
    if (next === snapEnabled) return;
    snapEnabled = next;
    persistSnapPreference();
    if (next) await ensureSnapMap();
    scheduleRender();
  }

  function setSnapRadius(value: number) {
    snapRadiusScreen = Math.min(SNAP_RADIUS_MAX, Math.max(SNAP_RADIUS_MIN, Math.round(value)));
    persistSnapPreference();
  }

  /** Snap radius converted from screen pixels to image pixels at the current zoom. */
  function snapRadiusImage() {
    if (viewScale <= 0) return snapRadiusScreen;
    return snapRadiusScreen / viewScale;
  }

  /**
   * Pull a point onto the nearest strong image edge. The input is returned
   * untouched when snapping is off, its map is missing, or the point sits in a
   * flat area (so freehand tracing of smooth regions is unaffected).
   *
   * The previous snapped point acts as a continuity anchor: an equally strong
   * parallel edge a few pixels away (an outline and its shadow) would otherwise
   * win on alternate events and comb the selection with unmarked strips.
   */
  function snapPoint(point: MaskPoint): MaskPoint {
    if (!snapEnabled || !snapMap || !snapAvailable) return point;
    const radius = snapRadiusImage();
    const previous = lastSnapPoint;
    const result = snapToEdge(snapMap, point.x, point.y, radius, undefined, {
      x: previous?.x ?? point.x,
      y: previous?.y ?? point.y,
      stepImagePx: previous
        ? Math.max(1, Math.hypot(point.x - previous.x, point.y - previous.y))
        : radius
    });
    if (!result.snapped) return point;
    const snapped = { x: result.x, y: result.y };
    lastSnapPoint = snapped;
    return snapped;
  }

  /** Forget the continuity anchor; called whenever a shape drag starts. */
  function resetSnapAnchor() {
    lastSnapPoint = null;
  }

  /**
   * Snap a run of path points, densifying wherever two of them end up more
   * than two radii apart: a straight chord between distant snapped points cuts
   * into a curved contour, and each added midpoint is snapped in turn.
   */
  function snapPathPoints(points: MaskPoint[]): MaskPoint[] {
    if (!snapEnabled || !snapMap || !snapAvailable) return points;
    const maxGap = Math.max(2, snapRadiusImage() * 2);
    const snapped: MaskPoint[] = [];
    for (const point of points) {
      const previous = snapped[snapped.length - 1];
      if (previous) {
        const gap = Math.hypot(point.x - previous.x, point.y - previous.y);
        if (gap > maxGap) {
          const steps = Math.min(8, Math.ceil(gap / maxGap));
          for (let step = 1; step < steps; step += 1) {
            snapped.push(
              snapPoint({
                x: previous.x + ((point.x - previous.x) * step) / steps,
                y: previous.y + ((point.y - previous.y) * step) / steps
              })
            );
          }
        }
      }
      snapped.push(snapPoint(point));
    }
    return snapped;
  }

  function setView(next: 'overlay' | 'maskOnly') {
    if (view === next) return;
    view = next;
    scheduleRender();
  }

  // The stage is the visible viewport; the zoom layer inside it is sized to
  // the image's natural pixels and moved with the view transform, so screen
  // coordinates map back through that transform into image coordinates.
  function stageRect() {
    return stageEl?.getBoundingClientRect() ?? null;
  }

  function toLayerPoint(event: { clientX: number; clientY: number }, rect: DOMRect | null = stageRect()) {
    if (!rect) return null;
    return {
      x: (event.clientX - rect.left - viewX) / viewScale,
      y: (event.clientY - rect.top - viewY) / viewScale
    };
  }

  function toImagePoint(
    event: { clientX: number; clientY: number },
    rect: DOMRect | null = stageRect()
  ): MaskPoint | null {
    const layer = toLayerPoint(event, rect);
    if (!layer || !doc) return null;
    return {
      x: Math.min(doc.width, Math.max(0, layer.x)),
      y: Math.min(doc.height, Math.max(0, layer.y))
    };
  }

  function brushImageSize() {
    if (!doc || viewScale <= 0) return 1;
    return Math.max(1, brushScreen / viewScale);
  }

  function updateCursor(event: PointerEvent, rect: DOMRect | null) {
    if (!cursorEl || !rect) return;
    const layer = toLayerPoint(event, rect);
    if (!layer) return;
    // Write the position straight to the element: a Svelte state update per
    // pointermove would re-render the template dozens of times per second.
    cursorEl.style.left = `${layer.x}px`;
    cursorEl.style.top = `${layer.y}px`;
    cursorVisible = true;
  }

  function clampView() {
    const rect = stageRect();
    if (!rect || !doc) return;
    // Keep the image reachable: when it is larger than the stage pin it to the
    // edges, when it is smaller allow centering it freely.
    const rangeX = rect.width - doc.width * viewScale;
    const rangeY = rect.height - doc.height * viewScale;
    viewX = Math.min(Math.max(0, rangeX), Math.max(Math.min(0, rangeX), viewX));
    viewY = Math.min(Math.max(0, rangeY), Math.max(Math.min(0, rangeY), viewY));
  }

  /** Fit the whole image into the stage without upscaling it. */
  function fitView() {
    const rect = stageRect();
    if (!rect || !doc) return;
    const fit = Math.min(rect.width / doc.width, rect.height / doc.height, 1);
    viewScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, fit));
    viewX = (rect.width - doc.width * viewScale) / 2;
    viewY = (rect.height - doc.height * viewScale) / 2;
    scheduleRender();
  }

  /** Jump to an exact zoom level (1 = one image pixel per screen pixel). */
  function zoomTo(scale: number) {
    const rect = stageRect();
    if (!rect || !doc) return;
    viewScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
    viewX = (rect.width - doc.width * viewScale) / 2;
    viewY = (rect.height - doc.height * viewScale) / 2;
    clampView();
    scheduleRender();
  }

  function zoomAt(clientX: number, clientY: number, nextScale: number) {
    const rect = stageRect();
    if (!rect) return;
    const scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextScale));
    if (scale === viewScale) return;
    const layerX = (clientX - rect.left - viewX) / viewScale;
    const layerY = (clientY - rect.top - viewY) / viewScale;
    viewScale = scale;
    viewX = clientX - rect.left - layerX * scale;
    viewY = clientY - rect.top - layerY * scale;
    clampView();
    scheduleRender();
  }

  function zoomAtCenter(factor: number) {
    const rect = stageRect();
    if (!rect) return;
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, viewScale * factor);
  }

  function handleWheel(event: WheelEvent) {
    if (!doc || busy) return;
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, viewScale * (event.deltaY < 0 ? 1.15 : 1 / 1.15));
  }

  function updateHistoryFlags() {
    canUndo = Boolean(doc?.canUndo());
    canRedo = Boolean(doc?.canRedo());
  }

  function handlePointerDown(event: PointerEvent) {
    if (!doc || !overlayEl || busy) return;
    const rect = stageRect();
    if (event.button === 1 || spacePressed || tool === 'pan') {
      event.preventDefault();
      overlayEl.setPointerCapture(event.pointerId);
      panning = true;
      panLastX = event.clientX;
      panLastY = event.clientY;
      return;
    }
    if (event.button !== 0) return;
    // Ignore presses on the stage margins around the image: painting clamps
    // to the image bounds, and a stray dab on the nearest edge is never what
    // a click outside the picture means.
    const layer = toLayerPoint(event, rect);
    if (!layer || layer.x < 0 || layer.y < 0 || layer.x > doc.width || layer.y > doc.height) return;
    const point = toImagePoint(event, rect);
    if (!point) return;
    if (event.shiftKey && tool === 'brush' && lastBrushEnd) {
      doc.beginStroke('brush', brushImageSize(), lastBrushEnd);
      doc.extendStroke([point]);
      doc.endStroke();
      lastBrushEnd = point;
      paintedAny = true;
      dirty = true;
      publishCoverage();
      updateHistoryFlags();
      scheduleSmoothSync(smoothPx > 0 ? 150 : 0);
      scheduleRender();
      return;
    }
    overlayEl.setPointerCapture(event.pointerId);
    drawing = true;
    const mode: MaskShapeMode = event.altKey ? (shapeMode === 'add' ? 'erase' : 'add') : shapeMode;
    resetSnapAnchor();
    doc.beginStroke(tool, brushImageSize(), snapPoint(point), mode);
    paintedAny = true;
    dirty = true;
    updateHistoryFlags();
    updateCursor(event, rect);
    scheduleRender();
  }

  function handlePointerMove(event: PointerEvent) {
    if (!doc) return;
    // One layout read per move; the coalesced events reuse it.
    const rect = stageRect();
    if (panning) {
      viewX += event.clientX - panLastX;
      viewY += event.clientY - panLastY;
      panLastX = event.clientX;
      panLastY = event.clientY;
      clampView();
      scheduleRender();
      return;
    }
    updateCursor(event, rect);
    if (!drawing) return;
    const coalesced =
      typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [event];
    const points = snapPathPoints(
      coalesced
        .map((candidate) => toImagePoint(candidate, rect))
        .filter((point): point is MaskPoint => point !== null)
    );
    if (!points.length) return;
    doc.extendStroke(points, event.shiftKey);
    if (tool === 'brush' || tool === 'eraser') liveCoverageDirty = true;
    scheduleRender();
  }

  function handlePointerUp(event: PointerEvent) {
    if (panning) {
      panning = false;
      if (overlayEl?.hasPointerCapture(event.pointerId)) {
        overlayEl.releasePointerCapture(event.pointerId);
      }
      return;
    }
    if (!doc || !drawing) return;
    drawing = false;
    if (tool === 'rect' || tool === 'ellipse') {
      const end = toImagePoint(event, stageRect());
      if (end) doc.extendStroke([end], event.shiftKey);
    }
    if (overlayEl?.hasPointerCapture(event.pointerId)) {
      overlayEl.releasePointerCapture(event.pointerId);
    }
    doc.endStroke();
    if (tool === 'brush') lastBrushEnd = toImagePoint(event, stageRect());
    // Commit the number synchronously so the readout never sits on the
    // in-flight estimate while the debounced smoothing sync catches up.
    publishCoverage();
    scheduleRender();
    scheduleSmoothSync(smoothPx > 0 ? 150 : 0);
    updateHistoryFlags();
  }

  function handlePointerLeave() {
    cursorVisible = false;
    pointerOverCanvas = false;
    spacePressed = false;
  }

  function setBrushSize(value: number) {
    brushScreen = Math.min(160, Math.max(4, Math.round(value)));
  }

  function setSmooth(value: number) {
    smoothPx = Math.min(MAX_SMOOTH_PX, Math.max(0, Math.round(value)));
    scheduleSmoothSync(120);
  }

  /** Recompute the processed view (debounced so drags stay smooth). */
  function scheduleSmoothSync(delay = 150) {
    if (smoothTimer !== null) clearTimeout(smoothTimer);
    smoothTimer = setTimeout(() => {
      smoothTimer = null;
      if (!doc) return;
      doc.setSmoothing(smoothPx);
      publishCoverage();
      scheduleRender();
      // The smoothed view is what the gap-fill pass reads, so it must be
      // repainted before the next request goes out.
      scheduleRegion(150);
    }, delay);
  }

  /**
   * Common tail for whole-document edits (undo, redo, clear, invert): publish
   * the new coverage right away so the readout tracks the canvas, then let the
   * debounced smoothing sync and gap-fill pass catch up.
   */
  function afterHistoryChange() {
    publishCoverage();
    scheduleRender();
    scheduleSmoothSync(smoothPx > 0 ? 150 : 0);
  }

  async function undo() {
    if (!doc || busy || !doc.canUndo()) return;
    // Undo replays the command list and import commands decode async, so
    // guard re-entry with the busy state (pointer events check it too).
    busy = true;
    try {
      await doc.undo();
      lastBrushEnd = null;
      dirty = true;
      updateHistoryFlags();
      afterHistoryChange();
    } finally {
      busy = false;
    }
  }

  async function redo() {
    if (!doc || busy || !doc.canRedo()) return;
    busy = true;
    try {
      await doc.redo();
      lastBrushEnd = null;
      dirty = true;
      updateHistoryFlags();
      afterHistoryChange();
    } finally {
      busy = false;
    }
  }

  function clearMask() {
    if (!doc) return;
    doc.clear();
    lastBrushEnd = null;
    paintedAny = false;
    dirty = true;
    updateHistoryFlags();
    afterHistoryChange();
  }

  function invertMask() {
    if (!doc) return;
    doc.invert();
    lastBrushEnd = null;
    dirty = true;
    updateHistoryFlags();
    afterHistoryChange();
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
    // An imported mask replaces any existing selection by default; with marks
    // on the canvas the user picks replace or merge, and either way the import
    // is a single undoable step.
    let mode: MaskImportMode = 'replace';
    if (coverage > 0) {
      const replace = await confirmStore.confirm({
        title: get(t).maskEditor.uploadMask,
        message: get(t).maskEditor.uploadChooseMode,
        confirmLabel: get(t).maskEditor.uploadReplace,
        cancelLabel: get(t).maskEditor.uploadMerge,
        closeLabel: get(t).confirm.cancel,
        variant: 'default'
      });
      mode = replace ? 'replace' : 'merge';
    }
    busy = true;
    try {
      let result;
      try {
        result = await doc.importFromPng(file, mode);
      } catch (error) {
        if (!(error instanceof MaskImportError) || error.code !== 'size' ||
          Math.abs(error.width / error.height / (naturalWidth / naturalHeight) - 1) > 0.01) throw error;
        const confirmed = await confirmStore.confirm({
          title: get(t).maskEditor.uploadMask,
          message: get(t).maskEditor.uploadScalePrompt(error.width, error.height, naturalWidth, naturalHeight),
          confirmLabel: get(t).maskEditor.uploadScale,
          cancelLabel: get(t).confirm.cancel,
          closeLabel: get(t).confirm.cancel,
          variant: 'default'
        });
        if (!confirmed) return;
        result = await doc.importFromPng(file, mode, true);
      }
      if (result.blackWhite) onNotice(get(t).maskEditor.uploadBlackWhiteNotice);
      lastBrushEnd = null;
      uploadedAny = true;
      doc.syncProcessed();
      publishCoverage();
      dirty = true;
      updateHistoryFlags();
      scheduleRender();
      scheduleRegion(150);
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
      // Freeze the inputs the export reads, in order: the smoothed view first,
      // then the additions computed from it. Without the first step a pending
      // repaint could hand the worker the raw marks while the PNG renders the
      // smoothed contour, and the two would not add up.
      doc.setSmoothing(smoothPx);
      await refreshRegion();
      const result = await doc.exportPng({ smoothing: smoothPx });
      if (result.coverage <= 0) {
        onError(get(t).maskEditor.noAreaMarked);
        return;
      }
      dirty = false;
      // U2: keep the pre-export strokes/protection/parameters alongside the
      // exported PNG so a later reopen can restore them exactly, instead of
      // reimporting the already-processed export as a fresh, undo-less start.
      const [marks, protectedLayer] = await Promise.all([
        doc.exportMarksSnapshot(),
        doc.exportProtectedSnapshot()
      ]);
      onApply({
        sourceId,
        blob: result.blob,
        width: doc.width,
        height: doc.height,
        coverage: result.coverage,
        previewUrl: URL.createObjectURL(result.blob),
        // What the user did in *this* session outranks whatever the mask was
        // before it was opened here: a reopened and re-saved mask must not be
        // relabelled as an upload.
        origin: paintedAny ? 'painted' : uploadedAny ? 'uploaded' : (existingMask?.origin ?? 'uploaded'),
        editorState: { marks, protected: protectedLayer, smoothPx, region: regionPrefs }
      });
    } catch {
      onError(get(t).maskEditor.importFailed);
    } finally {
      busy = false;
    }
  }

  function saveWithoutMask() {
    if (busy || !hasExistingMask) return;
    dirty = false;
    onRemove();
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

  function handleKeydownCapture(event: KeyboardEvent) {
    if (!open) return;
    // Escape during a shape drag should discard that drag, not close the whole
    // dialog. `use:dialog` listens on document in the capture phase, so this
    // window-capture handler runs first and can consume the event. Snapping
    // makes a mistimed drag more likely, which is why this exists.
    if (event.key === 'Escape' && doc?.hasActiveShape()) {
      event.preventDefault();
      event.stopPropagation();
      doc.cancelActiveShape();
      scheduleRender();
    }
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
    if (event.key === ' ' && pointerOverCanvas) {
      // Hold space to pan; only when the pointer is over the canvas so the key
      // still activates focused controls.
      event.preventDefault();
      spacePressed = true;
      return;
    }
    if (key === 'b') tool = 'brush';
    else if (key === 'e') tool = 'eraser';
    else if (key === 'r') tool = 'rect';
    else if (key === 'o') tool = 'ellipse';
    else if (key === 'l') tool = 'lasso';
    else if (key === 'h') tool = 'pan';
    else if (key === 's') void setSnapEnabled(!snapEnabled);
    else if (key === 'g') toggleAutoFill();
    else if (key === '0') fitView();
    else if (event.key === '[') {
      event.preventDefault();
      setBrushSize(brushScreen - 8);
    } else if (event.key === ']') {
      event.preventDefault();
      setBrushSize(brushScreen + 8);
    }
  }

  function handleKeyup(event: KeyboardEvent) {
    if (event.key === ' ') spacePressed = false;
  }
</script>

<svelte:window
  on:keydown|capture={handleKeydownCapture}
  on:keydown={handleKeydown}
  on:keyup={handleKeyup}
/>

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
          <p class="mt-1 flex items-center gap-1.5 text-xs text-emerald-400">
            <span class="inline-block h-2 w-2 shrink-0 rounded-full bg-emerald-400/80" aria-hidden="true"></span>
            {$t.maskEditor.greenHint}
          </p>
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
        <!-- The stage needs a definite height of its own: h-full would collapse
            inside this auto-height flex area and break the fit-to-stage math. -->
        <div bind:this={stageEl} class="relative h-[52vh] w-full overflow-hidden rounded-lg sm:h-[58vh]">
          <div
            class="absolute left-0 top-0"
            style={`transform:translate(${viewX}px, ${viewY}px) scale(${viewScale});transform-origin:0 0;width:${naturalWidth || 1}px;height:${naturalHeight || 1}px`}
          >
            <img
              src={imageUrl}
              alt={label}
              class="block h-full w-full select-none"
              draggable="false"
            />
            {#if ready}
              <span
                bind:this={cursorEl}
                class="pointer-events-none absolute rounded-full border border-emerald-400/80 bg-emerald-400/10"
                class:hidden={cursorHidden}
                style={`width:${brushRingSize}px;height:${brushRingSize}px;transform:translate(-50%,-50%)`}
                aria-hidden="true"
              ></span>
            {/if}
          </div>
          {#if ready}
            <!-- The overlay is a viewport-sized sibling of the zoom layer: it
                covers the whole stage and draws marks through the view
                transform, so canvas memory tracks the screen, not the image. -->
            <canvas
              bind:this={overlayEl}
              class={`absolute inset-0 h-full w-full touch-none ${canvasCursorClass}`}
              aria-label={$t.maskEditor.canvasLabel}
              on:pointerdown={handlePointerDown}
              on:pointermove={handlePointerMove}
              on:pointerup={handlePointerUp}
              on:pointercancel={handlePointerUp}
              on:pointerenter={() => (pointerOverCanvas = true)}
              on:pointerleave={handlePointerLeave}
              on:wheel={handleWheel}
              on:contextmenu|preventDefault
            ></canvas>
          {/if}
        </div>
      </div>

      <div class="space-y-2.5 border-t border-zinc-800 px-3 py-3">
        <div class="relative flex flex-wrap items-center gap-2">
          {#if showHelp}
            <div
              class="absolute bottom-full right-0 z-20 mb-2 w-80 rounded-xl border border-zinc-800 bg-zinc-900 p-3 shadow-xl"
              role="note"
              aria-label={$t.maskEditor.shortcutsTitle}
            >
              <div class="flex items-center justify-between gap-2">
                <p class="text-xs font-semibold text-zinc-100">{$t.maskEditor.shortcutsTitle}</p>
                <button
                  type="button"
                  class="control-focus rounded-md p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                  aria-label={$t.maskEditor.closeLabel}
                  on:click={() => (showHelp = false)}
                >
                  <X size={13} strokeWidth={2} aria-hidden="true" />
                </button>
              </div>
              <p class="mt-2 leading-6 text-zinc-400">{$t.maskEditor.shortcuts}</p>
            </div>
          {/if}

          {#if showRegionPanel}
            <div
              class="absolute bottom-full left-0 z-20 mb-2 w-80 space-y-3 rounded-xl border border-zinc-800 bg-zinc-900 p-3 shadow-xl"
              role="note"
              aria-label={$t.maskEditor.regionGroupLabel}
            >
              <div class="flex items-center justify-between gap-2">
                <p class="text-xs font-semibold text-zinc-100">{$t.maskEditor.regionGroupLabel}</p>
                <button
                  type="button"
                  class="control-focus rounded-md p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                  aria-label={$t.maskEditor.closeLabel}
                  on:click={() => (showRegionPanel = false)}
                >
                  <X size={13} strokeWidth={2} aria-hidden="true" />
                </button>
              </div>

              <div class="space-y-1">
                <p class="text-xs text-zinc-400">{$t.maskEditor.autoFillStrength}</p>
                <div class="flex items-center gap-1" role="group" aria-label={$t.maskEditor.autoFillStrength}>
                  {#each strengthOptions as option (option.value)}
                    <button
                      type="button"
                      class="mobile-touch-target control-focus flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
                      class:bg-emerald-600={regionPrefs.strength === option.value}
                      class:text-white={regionPrefs.strength === option.value}
                      class:text-zinc-300={regionPrefs.strength !== option.value}
                      class:hover:bg-zinc-800={regionPrefs.strength !== option.value}
                      aria-pressed={regionPrefs.strength === option.value}
                      on:click={() => setRegionPref({ strength: option.value })}
                    >
                      {option.label}
                    </button>
                  {/each}
                </div>
              </div>

              <div class="space-y-2">
                <label class="flex items-center justify-between gap-2 text-xs text-zinc-300">
                  <span>{$t.maskEditor.edgeSnap}</span>
                  <input
                    type="checkbox"
                    class="control-focus h-4 w-4 accent-emerald-600"
                    aria-label={$t.maskEditor.edgeSnap}
                    checked={regionPrefs.edgeSnap}
                    on:change={(event) =>
                      setRegionPref({ edgeSnap: (event.currentTarget as HTMLInputElement).checked })}
                  />
                </label>
                <p class="text-[11px] leading-4 text-zinc-500">{$t.maskEditor.edgeSnapHint}</p>
                {#if regionPrefs.edgeSnap}
                  <label class="flex items-center gap-2">
                    <input
                      type="range"
                      min={REGION_EDGE_RANGE_MIN}
                      max={REGION_EDGE_RANGE_MAX}
                      step="1"
                      class="control-focus h-1.5 w-full accent-emerald-600"
                      aria-label={$t.maskEditor.edgeSnapRange(edgeRangeDefault())}
                      value={regionPrefs.edgeRange ?? edgeRangeDefault()}
                      on:input={(event) =>
                        setRegionPref({ edgeRange: (event.currentTarget as HTMLInputElement).valueAsNumber })}
                    />
                    <span class="w-[74px] shrink-0 text-right text-xs tabular-nums text-zinc-400">
                      {$t.maskEditor.edgeSnapRange(regionPrefs.edgeRange ?? edgeRangeDefault())}
                    </span>
                  </label>
                {/if}
              </div>

              <label class="flex items-center gap-2">
                <input
                  type="range"
                  min="0"
                  max={REGION_GROW_MAX}
                  step="1"
                  class="control-focus h-1.5 w-full accent-emerald-600"
                  aria-label={$t.maskEditor.grow(regionPrefs.grow)}
                  title={$t.maskEditor.growHint}
                  value={regionPrefs.grow}
                  on:input={(event) =>
                    setRegionPref({ grow: (event.currentTarget as HTMLInputElement).valueAsNumber })}
                />
                <span class="w-[74px] shrink-0 text-right text-xs tabular-nums text-zinc-400">
                  {$t.maskEditor.grow(regionPrefs.grow)}
                </span>
              </label>

              <label class="flex items-center gap-2">
                <input
                  type="range"
                  min="0"
                  max={MAX_SMOOTH_PX}
                  step="2"
                  class="control-focus h-1.5 w-full accent-emerald-600"
                  aria-label={$t.maskEditor.smooth(smoothPx)}
                  title={$t.maskEditor.smoothHint}
                  value={smoothPx}
                  on:input={(event) => setSmooth((event.currentTarget as HTMLInputElement).valueAsNumber)}
                />
                <span class="w-[74px] shrink-0 text-right text-xs tabular-nums text-zinc-400">
                  {$t.maskEditor.smooth(smoothPx)}
                </span>
              </label>
            </div>
          {/if}

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
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={tool === 'rect'}
              class:text-white={tool === 'rect'}
              class:text-zinc-300={tool !== 'rect'}
              class:hover:bg-zinc-800={tool !== 'rect'}
              aria-pressed={tool === 'rect'}
              aria-label={$t.maskEditor.rect}
              title={$t.maskEditor.rect}
              on:click={() => (tool = 'rect')}
            >
              <Square size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.rect}</span>
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={tool === 'ellipse'}
              class:text-white={tool === 'ellipse'}
              class:text-zinc-300={tool !== 'ellipse'}
              class:hover:bg-zinc-800={tool !== 'ellipse'}
              aria-pressed={tool === 'ellipse'}
              aria-label={$t.maskEditor.ellipse}
              title={$t.maskEditor.ellipse}
              on:click={() => (tool = 'ellipse')}
            >
              <Circle size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.ellipse}</span>
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={tool === 'lasso'}
              class:text-white={tool === 'lasso'}
              class:text-zinc-300={tool !== 'lasso'}
              class:hover:bg-zinc-800={tool !== 'lasso'}
              aria-pressed={tool === 'lasso'}
              aria-label={$t.maskEditor.lasso}
              title={$t.maskEditor.lasso}
              on:click={() => (tool = 'lasso')}
            >
              <Lasso size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.lasso}</span>
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={tool === 'pan'}
              class:text-white={tool === 'pan'}
              class:text-zinc-300={tool !== 'pan'}
              class:hover:bg-zinc-800={tool !== 'pan'}
              aria-pressed={tool === 'pan'}
              aria-label={$t.maskEditor.pan}
              title={$t.maskEditor.pan}
              on:click={() => (tool = 'pan')}
            >
              <Hand size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.pan}</span>
            </button>
          </div>

          {#if snapAvailable}
            <div
              class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1"
              role="group"
              aria-label={$t.maskEditor.snapGroupLabel}
            >
              <button
                type="button"
                class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
                class:bg-emerald-600={snapEnabled}
                class:text-white={snapEnabled}
                class:text-zinc-300={!snapEnabled}
                class:hover:bg-zinc-800={!snapEnabled}
                aria-pressed={snapEnabled}
                aria-label={$t.maskEditor.snap}
                title={$t.maskEditor.snapHint}
                on:click={() => void setSnapEnabled(!snapEnabled)}
              >
                <Magnet size={15} strokeWidth={1.9} aria-hidden="true" />
                <span>{$t.maskEditor.snap}</span>
              </button>
              {#if snapEnabled}
                <label class="flex items-center gap-2 px-1.5">
                  <input
                    type="range"
                    min={SNAP_RADIUS_MIN}
                    max={SNAP_RADIUS_MAX}
                    step="1"
                    class="control-focus h-1.5 w-24 accent-emerald-600"
                    aria-label={$t.maskEditor.snapRadius(snapRadiusScreen)}
                    title={$t.maskEditor.snapRadius(snapRadiusScreen)}
                    value={snapRadiusScreen}
                    on:input={(event) =>
                      setSnapRadius((event.currentTarget as HTMLInputElement).valueAsNumber)}
                  />
                  <span class="w-[70px] shrink-0 text-xs tabular-nums text-zinc-400">
                    {$t.maskEditor.snapRadius(snapRadiusScreen)}
                  </span>
                </label>
              {/if}
            </div>
          {/if}

          <div
            class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1"
            role="group"
            aria-label={$t.maskEditor.regionGroupLabel}
          >
            <button
              type="button"
              class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
              class:bg-emerald-600={regionPrefs.enabled}
              class:text-white={regionPrefs.enabled}
              class:text-zinc-300={!regionPrefs.enabled}
              class:hover:bg-zinc-800={!regionPrefs.enabled}
              aria-pressed={regionPrefs.enabled}
              aria-label={$t.maskEditor.autoFill}
              title={$t.maskEditor.autoFillHint}
              on:click={toggleAutoFill}
            >
              <Sparkles size={15} strokeWidth={1.9} aria-hidden="true" />
              <span>{$t.maskEditor.autoFill}</span>
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800"
              class:bg-zinc-800={showRegionPanel}
              aria-label={$t.maskEditor.regionGroupLabel}
              title={$t.maskEditor.regionGroupLabel}
              aria-expanded={showRegionPanel}
              on:click={() => (showRegionPanel = !showRegionPanel)}
            >
              <SlidersHorizontal size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>

          {#if tool === 'rect' || tool === 'ellipse' || tool === 'lasso'}
            <div
              class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1"
              role="group"
              aria-label={$t.maskEditor.modeGroupLabel}
            >
              <button
                type="button"
                class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
                class:bg-emerald-600={shapeMode === 'add'}
                class:text-white={shapeMode === 'add'}
                class:text-zinc-300={shapeMode !== 'add'}
                class:hover:bg-zinc-800={shapeMode !== 'add'}
                aria-pressed={shapeMode === 'add'}
                aria-label={$t.maskEditor.addMode}
                title={$t.maskEditor.addMode}
                on:click={() => (shapeMode = 'add')}
              >
                <Plus size={15} strokeWidth={1.9} aria-hidden="true" />
                <span>{$t.maskEditor.addMode}</span>
              </button>
              <button
                type="button"
                class="mobile-touch-target control-focus flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
                class:bg-emerald-600={shapeMode === 'erase'}
                class:text-white={shapeMode === 'erase'}
                class:text-zinc-300={shapeMode !== 'erase'}
                class:hover:bg-zinc-800={shapeMode !== 'erase'}
                aria-pressed={shapeMode === 'erase'}
                aria-label={$t.maskEditor.subtractMode}
                title={$t.maskEditor.subtractMode}
                on:click={() => (shapeMode = 'erase')}
              >
                <Minus size={15} strokeWidth={1.9} aria-hidden="true" />
                <span>{$t.maskEditor.subtractMode}</span>
              </button>
            </div>
          {/if}

          {#if tool === 'brush' || tool === 'eraser'}
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
          {/if}

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

          <div class="flex items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1" role="group" aria-label={$t.maskEditor.zoomLabel}>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={viewScale <= MIN_ZOOM}
              aria-label={$t.maskEditor.zoomOut}
              title={$t.maskEditor.zoomOut}
              on:click={() => zoomAtCenter(1 / 1.25)}
            >
              <ZoomOut size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <span class="w-[42px] shrink-0 text-center text-xs tabular-nums text-zinc-400">
              {Math.round(viewScale * 100)}%
            </span>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={viewScale >= MAX_ZOOM}
              aria-label={$t.maskEditor.zoomIn}
              title={$t.maskEditor.zoomIn}
              on:click={() => zoomAtCenter(1.25)}
            >
              <ZoomIn size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md px-1.5 py-1.5 text-xs font-medium tabular-nums text-zinc-300 transition-colors hover:bg-zinc-800"
              class:bg-zinc-800={viewScale === 1}
              class:text-zinc-100={viewScale === 1}
              aria-pressed={viewScale === 1}
              aria-label={$t.maskEditor.zoom100}
              title={$t.maskEditor.zoom100}
              on:click={() => zoomTo(1)}
            >
              100%
            </button>
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800"
              aria-label={$t.maskEditor.fitCanvas}
              title={$t.maskEditor.fitCanvas}
              on:click={fitView}
            >
              <Maximize size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>

          <input
            bind:this={uploadInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
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

          <button
            type="button"
            class="mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800"
            aria-label={$t.maskEditor.shortcutsTitle}
            title={$t.maskEditor.shortcutsTitle}
            aria-expanded={showHelp}
            on:click={() => (showHelp = !showHelp)}
          >
            <CircleHelp size={15} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>

        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="min-w-0">
            <p class="text-xs tabular-nums text-zinc-400" aria-live="polite">
              {#if regionPending}
                {$t.maskEditor.coverageWithAuto(
                  (coverage * 100).toFixed(1),
                  $t.maskEditor.coveragePending
                )}
              {:else if autoCoverage > 0.0005}
                {$t.maskEditor.coverageWithAuto(
                  (coverage * 100).toFixed(1),
                  (autoCoverage * 100).toFixed(1)
                )}
              {:else}
                {$t.maskEditor.coverage((coverage * 100).toFixed(1))}
              {/if}
            </p>
            {#if autoHuge}
              <p class="mt-0.5 text-xs text-zinc-500">{$t.maskEditor.autoFillLargeHint}</p>
            {:else if !regionPending && coverage + autoCoverage > 0 && coverage + autoCoverage < 0.005}
              <p class="mt-0.5 text-xs text-zinc-500">{$t.maskEditor.coverageTooSmall}</p>
            {:else if !regionPending && coverage + autoCoverage > 0.95}
              <p class="mt-0.5 text-xs text-zinc-500">{$t.maskEditor.coverageTooLarge}</p>
            {/if}
          </div>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-lg px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
              on:click={requestClose}
            >
              {$t.maskEditor.cancel}
            </button>
            {#if hasExistingMask && coverage <= 0}
              <button
                type="button"
                class="mobile-touch-target control-focus rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={busy}
                title={$t.maskEditor.saveNoMask}
                on:click={saveWithoutMask}
              >
                {$t.maskEditor.saveNoMask}
              </button>
            {:else}
              <button
                type="button"
                class="mobile-touch-target control-focus rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={busy || coverage <= 0}
                title={coverage <= 0 ? $t.maskEditor.noAreaMarked : $t.maskEditor.apply}
                on:click={applyMask}
              >
                {$t.maskEditor.apply}
              </button>
            {/if}
          </div>
        </div>
      </div>
    </div>
  </div>
{/if}
