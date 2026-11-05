<script lang="ts">
  import { get } from 'svelte/store';
  import { tick, untrack } from 'svelte';
  import ArrowUpRight from 'lucide-svelte/icons/move-up-right';
  import Check from 'lucide-svelte/icons/check';
  import CircleHelp from 'lucide-svelte/icons/circle-help';
  import Circle from 'lucide-svelte/icons/circle';
  import Eraser from 'lucide-svelte/icons/eraser';
  import Eye from 'lucide-svelte/icons/eye';
  import EyeOff from 'lucide-svelte/icons/eye-off';
  import Hand from 'lucide-svelte/icons/hand';
  import Maximize from 'lucide-svelte/icons/maximize';
  import Pencil from 'lucide-svelte/icons/pencil';
  import Redo2 from 'lucide-svelte/icons/redo-2';
  import Square from 'lucide-svelte/icons/square';
  import Trash2 from 'lucide-svelte/icons/trash-2';
  import TypeIcon from 'lucide-svelte/icons/type';
  import Undo2 from 'lucide-svelte/icons/undo-2';
  import X from 'lucide-svelte/icons/x';
  import ZoomIn from 'lucide-svelte/icons/zoom-in';
  import ZoomOut from 'lucide-svelte/icons/zoom-out';
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { t } from '$lib/i18n';
  import { dialog } from '$lib/actions/dialog';
  import { confirmStore } from '$lib/stores/confirm';
  import { editSourceStore, type EditMarkup } from '$lib/stores/editSource';
  import { loadPrimaryImage, PrimaryImageError, type PrimaryImage } from '$lib/features/workspace/editPrimaryImage';
  import { markupLimits, type MarkupApplyPayload } from '$lib/features/workspace/markup';
  import {
    DEFAULT_MARKUP_COLOR,
    MARKUP_COLORS,
    MarkupDocument,
    MarkupDocumentError,
    markupFont,
    markupSizing,
    renderMarkupCommand,
    sanitizeMarkupText,
    type MarkupCommandInput,
    type MarkupTool
  } from '$lib/features/markup/markupDocument';
  import {
    clampToImage,
    clampView,
    fitView,
    isInsideImage,
    minViewScale,
    fitScale,
    MAX_VIEW_SCALE,
    imageToStage,
    panView,
    pinchView,
    stageToImage,
    viewportBackingSize,
    zoomViewAt,
    zoomViewByFactor,
    type MarkupPoint,
    type MarkupView
  } from '$lib/features/markup/markupViewport';

  let {
    open,
    sessionId,
    sourceId = '',
    label = '',
    existing = null,
    onApply,
    onRemove,
    onClose,
    onError
  }: {
    open: boolean;
    sessionId: number;
    sourceId?: string;
    label?: string;
    existing?: EditMarkup | null;
    /** Resolves true once the parent installed the markup (it then closes the editor). */
    onApply: (payload: MarkupApplyPayload) => Promise<boolean>;
    onRemove: () => void;
    onClose: () => void;
    onError: (message: string) => void;
  } = $props();

  type Gesture =
    | { kind: 'none' }
    | { kind: 'ignored' }
    | { kind: 'stroke'; pointerId: number }
    | { kind: 'shape'; pointerId: number; tool: 'arrow' | 'rect' | 'ellipse'; start: MarkupPoint; end: MarkupPoint; startStage: MarkupPoint }
    | { kind: 'pan'; pointerId: number; last: MarkupPoint }
    | { kind: 'pinch'; ids: [number, number]; start: MarkupView; center: MarkupPoint; distance: number }
    | { kind: 'text'; pointerId: number; startStage: MarkupPoint; at: MarkupPoint };

  const DRAW_TOOLS: MarkupTool[] = ['pen', 'arrow', 'rect', 'ellipse', 'text', 'eraser'];
  const SHORTCUT_TOOLS: Record<string, MarkupTool> = { p: 'pen', a: 'arrow', r: 'rect', o: 'ellipse', t: 'text', e: 'eraser', h: 'pan' };
  const MIN_SHAPE_SCREEN_PX = 3;
  const TAP_SLOP_PX = 8;

  // Reactive UI state. Anything touched on every pointer move stays out of here.
  let stageEl = $state<HTMLDivElement>();
  let canvasEl = $state<HTMLCanvasElement>();
  let textInputEl = $state<HTMLInputElement>();
  let ready = $state(false);
  let busy = $state(false);
  let loadError = $state('');
  let tool = $state<MarkupTool>('pen');
  let color = $state(DEFAULT_MARKUP_COLOR);
  let strokeWidth = $state(4);
  let textSize = $state(32);
  let sizing = $state(markupSizing(1, 1));
  let showMarkup = $state(true);
  let canUndo = $state(false);
  let canRedo = $state(false);
  let dirty = $state(false);
  let hasMarks = $state(false);
  let zoomPercent = $state(100);
  let atMinZoom = $state(false);
  let atMaxZoom = $state(false);
  let showHelp = $state(false);
  let spaceDown = $state(false);
  let shownView = $state<MarkupView>({ scale: 1, x: 0, y: 0 });
  let textDraft = $state<{ x: number; y: number; value: string } | null>(null);

  // Non-reactive working state (pointer-frequency paths).
  let doc: MarkupDocument | null = null;
  let primary: PrimaryImage | null = null;
  let openKey = '';
  let imageSize = { width: 1, height: 1 };
  let stage = { width: 0, height: 0 };
  let viewRaw: MarkupView = { scale: 1, x: 0, y: 0 };
  let autoFit = true;
  let gesture: Gesture = { kind: 'none' };
  const pointers = new Map<number, MarkupPoint>();
  let shapePreview: MarkupCommandInput | null = null;
  let renderHandle: number | null = null;
  let prepareToken = 0;
  let prepareAbort: AbortController | null = null;
  let viewCtx: CanvasRenderingContext2D | null = null;

  const strokeTool = $derived(tool === 'pen' || tool === 'arrow' || tool === 'rect' || tool === 'ellipse');
  const canApply = $derived(ready && !busy && hasMarks);
  const colorNames = $derived<Record<string, string>>({
    red: $t.markupEditor.colorRed,
    yellow: $t.markupEditor.colorYellow,
    blue: $t.markupEditor.colorBlue,
    green: $t.markupEditor.colorGreen,
    black: $t.markupEditor.colorBlack,
    white: $t.markupEditor.colorWhite
  });
  const toolDefs = $derived([
    { id: 'pen' as const, label: $t.markupEditor.pen, key: 'P', icon: Pencil },
    { id: 'arrow' as const, label: $t.markupEditor.arrow, key: 'A', icon: ArrowUpRight },
    { id: 'rect' as const, label: $t.markupEditor.rect, key: 'R', icon: Square },
    { id: 'ellipse' as const, label: $t.markupEditor.ellipse, key: 'O', icon: Circle },
    { id: 'text' as const, label: $t.markupEditor.text, key: 'T', icon: TypeIcon },
    { id: 'eraser' as const, label: $t.markupEditor.eraser, key: 'E', icon: Eraser },
    { id: 'pan' as const, label: $t.markupEditor.pan, key: 'H', icon: Hand }
  ]);
  const cursorStyle = $derived(
    spaceDown || tool === 'pan' ? 'grab' : tool === 'text' ? 'text' : 'crosshair'
  );

  // ---------------------------------------------------------------- lifecycle

  $effect(() => {
    if (!open) return;
    untrack(() => void prepare());
    return teardown;
  });

  async function prepare() {
    const token = ++prepareToken;
    teardownWork();
    ready = false;
    loadError = '';
    const controller = new AbortController();
    prepareAbort = controller;
    let image: PrimaryImage | null = null;
    let next: MarkupDocument | null = null;
    try {
      image = await loadPrimaryImage(get(editSourceStore), controller.signal);
      if (token !== prepareToken || image.sourceId !== sourceId) {
        image.bitmap.close();
        return;
      }
      const options = { maxPixels: markupLimits().maxPixels };
      const size = { width: image.width, height: image.height };
      const previous = existing && existing.sourceId === sourceId ? existing : null;
      if (previous?.editorState && previous.editorState.width === size.width && previous.editorState.height === size.height) {
        next = await MarkupDocument.fromState(previous.editorState, options);
      } else if (previous) {
        next = await MarkupDocument.fromPng(previous.blob, size, options);
      } else {
        next = MarkupDocument.create(size.width, size.height, options);
      }
      if (token !== prepareToken) {
        next.dispose();
        image.bitmap.close();
        return;
      }
      primary = image;
      doc = next;
      imageSize = size;
      openKey = next.stateKey;
      sizing = markupSizing(size.width, size.height);
      strokeWidth = sizing.strokeDefault;
      textSize = sizing.textDefault;
      showMarkup = true;
      autoFit = true;
      syncFlags();
      ready = true;
    } catch (error) {
      next?.dispose();
      image?.bitmap.close();
      if (token !== prepareToken || (error instanceof DOMException && error.name === 'AbortError')) return;
      const copy = get(t).markupEditor;
      loadError =
        error instanceof MarkupDocumentError && (error.code === 'too_large' || error.code === 'canvas')
          ? copy.errorCanvas
          : error instanceof PrimaryImageError || error instanceof MarkupDocumentError
            ? copy.errorLoad
            : copy.errorLoad;
      onError(loadError);
    }
  }

  function teardownWork() {
    if (renderHandle !== null) cancelAnimationFrame(renderHandle);
    renderHandle = null;
    pointers.clear();
    gesture = { kind: 'none' };
    shapePreview = null;
    textDraft = null;
    viewCtx = null;
    doc?.dispose();
    doc = null;
    primary?.bitmap.close();
    primary = null;
  }

  function teardown() {
    prepareToken += 1;
    prepareAbort?.abort();
    prepareAbort = null;
    teardownWork();
    ready = false;
    busy = false;
    dirty = false;
    canUndo = false;
    canRedo = false;
    hasMarks = false;
    spaceDown = false;
    showHelp = false;
  }

  // ------------------------------------------------------------------ viewport

  $effect(() => {
    const host = stageEl;
    const canvas = canvasEl;
    if (!host || !canvas || !ready) return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(host);
    const onWheel = (event: WheelEvent) => handleWheel(event);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    const onResize = () => measure();
    window.addEventListener('resize', onResize);
    measure();
    return () => {
      observer.disconnect();
      canvas.removeEventListener('wheel', onWheel);
      window.removeEventListener('resize', onResize);
    };
  });

  function measure() {
    if (!stageEl || !canvasEl || !ready) return;
    // Layout size, not the bounding box: the dialog's enter animation scales it.
    const width = stageEl.clientWidth;
    const height = stageEl.clientHeight;
    if (width < 1 || height < 1) return;
    stage = { width, height };
    const backing = viewportBackingSize(stage, window.devicePixelRatio || 1);
    if (canvasEl.width !== backing.width || canvasEl.height !== backing.height) {
      canvasEl.width = backing.width;
      canvasEl.height = backing.height;
      viewCtx = null;
    }
    viewRaw = autoFit ? fitView(stage, imageSize) : clampView(viewRaw, stage, imageSize);
    scheduleRender();
  }

  function setView(next: MarkupView, manual = true) {
    viewRaw = next;
    if (manual) autoFit = false;
    scheduleRender();
  }

  function scheduleRender() {
    if (renderHandle !== null) return;
    renderHandle = requestAnimationFrame(render);
  }

  function render() {
    renderHandle = null;
    const canvas = canvasEl;
    const document_ = doc;
    const image = primary;
    if (!canvas || !document_ || document_.isDisposed || !image || !ready) return;
    const ctx = (viewCtx ??= canvas.getContext('2d'));
    if (!ctx) return;
    const ratio = canvas.width / Math.max(1, stage.width);
    const { scale, x, y } = viewRaw;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(ratio * scale, 0, 0, ratio * scale, ratio * x, ratio * y);
    ctx.imageSmoothingEnabled = scale * ratio < 3;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(image.bitmap, 0, 0);
    if (showMarkup) ctx.drawImage(document_.layer as unknown as CanvasImageSource, 0, 0);
    if (shapePreview) renderMarkupCommand(ctx, shapePreview, imageSize);
    ctx.strokeStyle = 'rgba(255,255,255,0.28)';
    ctx.lineWidth = 1 / (ratio * scale);
    ctx.strokeRect(0, 0, imageSize.width, imageSize.height);
    const percent = Math.round(scale * 100);
    if (percent !== zoomPercent) zoomPercent = percent;
    const fit = fitScale(stage, imageSize);
    atMinZoom = scale <= minViewScale(fit) + 1e-9;
    atMaxZoom = scale >= Math.max(MAX_VIEW_SCALE, fit) - 1e-9;
    shownView = viewRaw;
  }

  function stageCenter(): MarkupPoint {
    return { x: stage.width / 2, y: stage.height / 2 };
  }

  function zoomBy(factor: number) {
    setView(zoomViewByFactor(viewRaw, factor, stageCenter(), stage, imageSize));
  }

  function zoomActual() {
    setView(zoomViewAt(viewRaw, 1, stageCenter(), stage, imageSize));
  }

  function fitCanvas() {
    autoFit = true;
    viewRaw = fitView(stage, imageSize);
    scheduleRender();
  }

  function handleWheel(event: WheelEvent) {
    event.preventDefault();
    if (!ready || busy || !canvasEl) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
    const factor = Math.exp(-event.deltaY * unit * (event.ctrlKey ? 0.01 : 0.0018));
    setView(zoomViewByFactor(viewRaw, factor, stagePoint(event), stage, imageSize));
  }

  // ------------------------------------------------------------------ document

  function syncFlags() {
    if (!doc || doc.isDisposed) return;
    canUndo = doc.canUndo;
    canRedo = doc.canRedo;
    dirty = doc.stateKey !== openKey;
    hasMarks = doc.historyDepth > 0 || doc.hasBase;
  }

  function afterChange() {
    syncFlags();
    scheduleRender();
  }

  function undo() {
    if (!ready || busy || !doc) return;
    cancelGesture();
    commitTextDraft();
    if (doc.undo()) afterChange();
  }

  function redo() {
    if (!ready || busy || !doc) return;
    cancelGesture();
    commitTextDraft();
    if (doc.redo()) afterChange();
  }

  function clearAll() {
    if (!ready || busy || !doc) return;
    cancelGesture();
    textDraft = null;
    if (doc.clear()) afterChange();
  }

  function setTool(next: MarkupTool) {
    if (busy) return;
    cancelGesture();
    commitTextDraft();
    tool = next;
  }

  // ------------------------------------------------------------------- pointer

  function stagePoint(event: { clientX: number; clientY: number }): MarkupPoint {
    const rect = canvasEl?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return { x: 0, y: 0 };
    // Normalise against any transform on an ancestor (the enter/exit animation).
    return {
      x: (event.clientX - rect.left) * (stage.width / rect.width),
      y: (event.clientY - rect.top) * (stage.height / rect.height)
    };
  }

  function imagePoint(point: MarkupPoint): MarkupPoint {
    return clampToImage(stageToImage(viewRaw, point), imageSize);
  }

  function distance(a: MarkupPoint, b: MarkupPoint) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function midpoint(a: MarkupPoint, b: MarkupPoint): MarkupPoint {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  /** Aborts an unfinished stroke / shape without recording anything. */
  function cancelGesture() {
    if (gesture.kind === 'stroke') doc?.cancelStroke();
    if (gesture.kind === 'stroke' || gesture.kind === 'shape') {
      shapePreview = null;
      gesture = pointers.size ? { kind: 'ignored' } : { kind: 'none' };
      afterChange();
    } else if (gesture.kind === 'text') {
      gesture = pointers.size ? { kind: 'ignored' } : { kind: 'none' };
    }
  }

  function releaseCapture(pointerId: number) {
    try {
      if (canvasEl?.hasPointerCapture(pointerId)) canvasEl.releasePointerCapture(pointerId);
    } catch {
      /* the pointer may already be gone */
    }
  }

  function startPinch() {
    cancelGesture();
    commitTextDraft();
    const ids = [...pointers.keys()].slice(0, 2) as [number, number];
    const a = pointers.get(ids[0]) as MarkupPoint;
    const b = pointers.get(ids[1]) as MarkupPoint;
    autoFit = false;
    gesture = { kind: 'pinch', ids, start: viewRaw, center: midpoint(a, b), distance: distance(a, b) };
  }

  function handlePointerDown(event: PointerEvent) {
    if (!ready || busy || !doc || !canvasEl) return;
    if (event.pointerType === 'mouse' && event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    if (textDraft) commitTextDraft();
    try {
      canvasEl.setPointerCapture(event.pointerId);
    } catch {
      /* capture is best effort */
    }
    const point = stagePoint(event);
    pointers.set(event.pointerId, point);
    if (pointers.size >= 2) {
      if (gesture.kind !== 'pinch') startPinch();
      return;
    }
    if (tool === 'pan' || spaceDown || event.button === 1) {
      gesture = { kind: 'pan', pointerId: event.pointerId, last: point };
      return;
    }
    const at = stageToImage(viewRaw, point);
    if (!isInsideImage(at, imageSize)) {
      gesture = { kind: 'ignored' };
      return;
    }
    if (tool === 'pen' || tool === 'eraser') {
      showMarkup = true;
      if (doc.hasActiveStroke) doc.cancelStroke();
      if (doc.beginStroke(tool === 'pen' ? 'pen' : 'erase', at, { color, width: strokeWidth })) {
        gesture = { kind: 'stroke', pointerId: event.pointerId };
        scheduleRender();
      } else {
        gesture = { kind: 'ignored' };
      }
    } else if (tool === 'arrow' || tool === 'rect' || tool === 'ellipse') {
      showMarkup = true;
      gesture = { kind: 'shape', pointerId: event.pointerId, tool, start: at, end: at, startStage: point };
    } else if (tool === 'text') {
      gesture = { kind: 'text', pointerId: event.pointerId, startStage: point, at };
    }
  }

  function constrainEnd(start: MarkupPoint, end: MarkupPoint, kind: 'arrow' | 'rect' | 'ellipse'): MarkupPoint {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    if (kind === 'arrow') {
      const length = Math.hypot(dx, dy);
      const step = Math.PI / 4;
      const angle = Math.round(Math.atan2(dy, dx) / step) * step;
      return clampToImage({ x: start.x + Math.cos(angle) * length, y: start.y + Math.sin(angle) * length }, imageSize);
    }
    const side = Math.max(Math.abs(dx), Math.abs(dy));
    return clampToImage({ x: start.x + Math.sign(dx || 1) * side, y: start.y + Math.sign(dy || 1) * side }, imageSize);
  }

  function shapeCommand(g: Extract<Gesture, { kind: 'shape' }>): MarkupCommandInput {
    return {
      kind: g.tool,
      color,
      width: strokeWidth,
      x1: g.start.x,
      y1: g.start.y,
      x2: g.end.x,
      y2: g.end.y
    };
  }

  function handlePointerMove(event: PointerEvent) {
    if (!pointers.has(event.pointerId) || !doc) return;
    const point = stagePoint(event);
    pointers.set(event.pointerId, point);
    const g = gesture;
    switch (g.kind) {
      case 'pinch': {
        const a = pointers.get(g.ids[0]);
        const b = pointers.get(g.ids[1]);
        if (!a || !b) return;
        setView(pinchView(g.start, g.center, g.distance, midpoint(a, b), distance(a, b), stage, imageSize));
        return;
      }
      case 'pan': {
        if (g.pointerId !== event.pointerId) return;
        setView(panView(viewRaw, point.x - g.last.x, point.y - g.last.y, stage, imageSize));
        g.last = point;
        return;
      }
      case 'stroke': {
        if (g.pointerId !== event.pointerId) return;
        const batch = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [];
        for (const sample of batch.length ? batch : [event]) {
          doc.extendStroke(imagePoint(stagePoint(sample)));
        }
        scheduleRender();
        return;
      }
      case 'shape': {
        if (g.pointerId !== event.pointerId) return;
        let end = imagePoint(point);
        if (event.shiftKey) end = constrainEnd(g.start, end, g.tool);
        g.end = end;
        shapePreview = shapeCommand(g);
        scheduleRender();
        return;
      }
      case 'text': {
        if (g.pointerId === event.pointerId && distance(point, g.startStage) > TAP_SLOP_PX) {
          gesture = { kind: 'ignored' };
        }
        return;
      }
      default:
        return;
    }
  }

  function finishPointer(event: PointerEvent, cancelled: boolean) {
    if (!pointers.has(event.pointerId)) return;
    const g = gesture;
    pointers.delete(event.pointerId);
    releaseCapture(event.pointerId);
    if (g.kind === 'stroke' && g.pointerId === event.pointerId) {
      if (cancelled) doc?.cancelStroke();
      else doc?.endStroke();
      gesture = { kind: 'none' };
      afterChange();
    } else if (g.kind === 'shape' && g.pointerId === event.pointerId) {
      gesture = { kind: 'none' };
      const committed = !cancelled && distance(imageToStage(viewRaw, g.start), imageToStage(viewRaw, g.end)) >= MIN_SHAPE_SCREEN_PX;
      if (committed) doc?.commit(shapeCommand(g));
      shapePreview = null;
      afterChange();
    } else if (g.kind === 'pan' && g.pointerId === event.pointerId) {
      gesture = { kind: 'none' };
    } else if (g.kind === 'text' && g.pointerId === event.pointerId) {
      gesture = { kind: 'none' };
      if (!cancelled) openTextDraft(g.at);
    } else if (g.kind === 'pinch') {
      // Lifting one finger ends the gesture; nothing is drawn until all fingers are up.
      gesture = pointers.size ? { kind: 'ignored' } : { kind: 'none' };
    }
    if (!pointers.size && gesture.kind === 'ignored') gesture = { kind: 'none' };
  }

  function handlePointerUp(event: PointerEvent) {
    finishPointer(event, false);
  }

  function handlePointerCancel(event: PointerEvent) {
    finishPointer(event, true);
  }

  function handleLostCapture(event: PointerEvent) {
    // Normal pointerup already cleared the pointer; this only fires for real capture loss.
    finishPointer(event, true);
  }

  function cancelEverything() {
    if (!ready) return;
    cancelGesture();
    pointers.clear();
    gesture = { kind: 'none' };
    spaceDown = false;
  }

  // ---------------------------------------------------------------------- text

  function openTextDraft(at: MarkupPoint) {
    textDraft = { x: at.x, y: at.y, value: '' };
    void tick().then(() => textInputEl?.focus());
  }

  function commitTextDraft() {
    const draft = textDraft;
    if (!draft) return;
    textDraft = null;
    const value = sanitizeMarkupText(draft.value);
    if (!value || !doc) return;
    showMarkup = true;
    doc.commit({ kind: 'text', color, size: textSize, x: draft.x, y: draft.y, text: value });
    afterChange();
  }

  function cancelTextDraft() {
    textDraft = null;
  }

  function handleTextKeydown(event: KeyboardEvent) {
    event.stopPropagation();
    if (event.key === 'Enter') {
      // Enter while composing selects an IME candidate; it must not submit.
      if (event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      commitTextDraft();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelTextDraft();
    }
  }

  const textBox = $derived.by(() => {
    if (!textDraft) return null;
    const point = imageToStage(shownView, textDraft);
    const size = textSize * shownView.scale;
    return {
      left: Math.min(Math.max(0, point.x), Math.max(0, stage.width - 48)),
      top: Math.min(Math.max(0, point.y), Math.max(0, stage.height - 28)),
      size
    };
  });

  // ------------------------------------------------------------------ keyboard

  function isEditableTarget(target: EventTarget | null) {
    const element = target instanceof HTMLElement ? target : null;
    return Boolean(element?.closest('input, textarea, select') || element?.isContentEditable);
  }

  function handleKeydownCapture(event: KeyboardEvent) {
    if (!open || event.key !== 'Escape') return;
    // Escape peels off the innermost thing first: text input, then an unfinished
    // stroke or shape, and only then the dialog (handled by use:dialog).
    if (textDraft) {
      event.preventDefault();
      event.stopPropagation();
      cancelTextDraft();
    } else if (gesture.kind === 'stroke' || gesture.kind === 'shape') {
      event.preventDefault();
      event.stopPropagation();
      cancelGesture();
    } else if (showHelp) {
      event.preventDefault();
      event.stopPropagation();
      showHelp = false;
    }
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!open || !ready || get(confirmStore).request) return;
    if (isEditableTarget(event.target)) return;
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
    if (meta || event.altKey) return;
    if (event.key === ' ') {
      event.preventDefault();
      if (!event.repeat) spaceDown = true;
      return;
    }
    if (key === '0') {
      event.preventDefault();
      fitCanvas();
    } else if (key === '1') {
      event.preventDefault();
      zoomActual();
    } else if (key === '=' || key === '+') {
      event.preventDefault();
      zoomBy(1.25);
    } else if (key === '-') {
      event.preventDefault();
      zoomBy(1 / 1.25);
    } else if (SHORTCUT_TOOLS[key]) {
      event.preventDefault();
      setTool(SHORTCUT_TOOLS[key]);
    }
  }

  function handleKeyup(event: KeyboardEvent) {
    if (event.key === ' ' && spaceDown) {
      event.preventDefault();
      spaceDown = false;
    }
  }

  // ----------------------------------------------------------------- lifecycle

  async function requestClose() {
    if (busy) return;
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

  function exportErrorMessage(error: unknown) {
    const copy = get(t).markupEditor;
    if (error instanceof MarkupDocumentError) {
      switch (error.code) {
        case 'empty':
          return copy.errorEmpty;
        case 'opaque':
          return copy.errorOpaque;
        case 'canvas':
        case 'too_large':
          return copy.errorCanvas;
        default:
          return copy.errorExport;
      }
    }
    return copy.errorExport;
  }

  async function applyMarkup() {
    if (!ready || busy || !doc) return;
    commitTextDraft();
    cancelGesture();
    const token = prepareToken;
    const current = doc;
    busy = true;
    try {
      const exported = await current.exportApplied();
      if (token !== prepareToken || !open) return;
      const painted = dirty || !existing;
      const accepted = await onApply({
        sessionId,
        sourceId,
        blob: exported.blob,
        width: exported.width,
        height: exported.height,
        editorState: exported.state,
        origin: painted ? 'painted' : (existing?.origin ?? 'uploaded')
      });
      if (accepted && token === prepareToken) dirty = false;
    } catch (error) {
      if (token === prepareToken) onError(exportErrorMessage(error));
    } finally {
      if (token === prepareToken) busy = false;
    }
  }

  async function removeApplied() {
    if (busy) return;
    const confirmed = await confirmStore.confirm({
      title: get(t).markupEditor.removeConfirmTitle,
      message: get(t).markupEditor.removeConfirmMessage,
      confirmLabel: get(t).markupEditor.removeMarkup,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      variant: 'danger'
    });
    if (confirmed && open) onRemove();
  }

  const group = 'flex shrink-0 items-center gap-1 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1';
  const toolButton =
    'mobile-touch-target control-focus flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors';
  const iconButton =
    'mobile-touch-target control-focus rounded-md p-1.5 text-zinc-300 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40';
</script>

<svelte:window
  onkeydowncapture={handleKeydownCapture}
  onkeydown={handleKeydown}
  onkeyup={handleKeyup}
  onblur={cancelEverything}
/>
<svelte:document onvisibilitychange={() => document.hidden && cancelEverything()} />

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
      aria-label={$t.markupEditor.closeLabel}
      onclick={requestClose}
    ></button>
    <div
      class="mobile-dvh-dialog overlay-panel relative flex h-full max-h-[calc(100vh-32px)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950"
      in:dialogIn
      out:dialogOut
      aria-labelledby="markup-editor-title"
      use:dialog={{ open, onClose: requestClose }}
    >
      <div class="flex shrink-0 items-start justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div class="min-w-0">
          <h2 id="markup-editor-title" class="truncate text-sm font-semibold text-zinc-100">
            {$t.markupEditor.title(label)}
          </h2>
          <p class="mt-1 flex items-center gap-1.5 text-xs text-emerald-400">
            <span class="inline-block h-2 w-2 shrink-0 rounded-full bg-emerald-400/80" aria-hidden="true"></span>
            {$t.markupEditor.hint}
          </p>
        </div>
        <button
          type="button"
          class="mobile-touch-target control-focus flex items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
          data-autofocus
          aria-label={$t.markupEditor.closeLabel}
          title={$t.markupEditor.closeLabel}
          onclick={requestClose}
        >
          <X size={16} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>

      <div class="shrink-0 space-y-2 border-b border-zinc-800/70 px-3 py-2">
        <div class="flex items-center gap-2 overflow-x-auto" role="toolbar" aria-label={$t.markupEditor.toolsLabel}>
          <div class={group} role="group" aria-label={$t.markupEditor.toolsLabel}>
            {#each toolDefs as def (def.id)}
              {@const Icon = def.icon}
              <button
                type="button"
                class={[toolButton, tool === def.id ? 'bg-emerald-600 text-white' : 'text-zinc-300 hover:bg-zinc-800']}
                aria-pressed={tool === def.id}
                aria-label={def.label}
                title={`${def.label} (${def.key})`}
                disabled={!ready || busy}
                onclick={() => setTool(def.id)}
              >
                <Icon size={15} strokeWidth={1.9} aria-hidden="true" />
                <span>{def.label}</span>
              </button>
            {/each}
          </div>

          <div class={group} role="group">
            <button type="button" class={iconButton} disabled={!canUndo || busy} aria-label={$t.markupEditor.undo} title={$t.markupEditor.undo} onclick={undo}>
              <Undo2 size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button type="button" class={iconButton} disabled={!canRedo || busy} aria-label={$t.markupEditor.redo} title={$t.markupEditor.redo} onclick={redo}>
              <Redo2 size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button type="button" class={iconButton} disabled={!hasMarks || busy} aria-label={$t.markupEditor.clear} title={$t.markupEditor.clear} onclick={clearAll}>
              <Trash2 size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <button
              type="button"
              class={iconButton}
              disabled={!ready}
              aria-pressed={!showMarkup}
              aria-label={showMarkup ? $t.markupEditor.hideMarkup : $t.markupEditor.showMarkup}
              title={showMarkup ? $t.markupEditor.hideMarkup : $t.markupEditor.showMarkup}
              onclick={() => {
                showMarkup = !showMarkup;
                scheduleRender();
              }}
            >
              {#if showMarkup}
                <Eye size={15} strokeWidth={1.9} aria-hidden="true" />
              {:else}
                <EyeOff size={15} strokeWidth={1.9} aria-hidden="true" />
              {/if}
            </button>
            <button
              type="button"
              class={iconButton}
              aria-label={$t.markupEditor.shortcutsTitle}
              title={$t.markupEditor.shortcutsTitle}
              aria-expanded={showHelp}
              onclick={() => (showHelp = !showHelp)}
            >
              <CircleHelp size={15} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          {#if strokeTool || tool === 'text'}
            <div class={group} role="group" aria-label={$t.markupEditor.colorLabel}>
              {#each MARKUP_COLORS as swatch (swatch.id)}
                <button
                  type="button"
                  class="mobile-touch-target control-focus flex shrink-0 items-center justify-center rounded-md transition-colors hover:bg-zinc-800"
                  aria-pressed={color === swatch.value}
                  aria-label={colorNames[swatch.id]}
                  title={colorNames[swatch.id]}
                  disabled={busy}
                  onclick={() => (color = swatch.value)}
                >
                  <span
                    class={['block h-6 w-6 rounded-full border', color === swatch.value ? 'border-emerald-400 ring-2 ring-emerald-400' : 'border-zinc-600']}
                    style={`background:${swatch.value}`}
                  ></span>
                </button>
              {/each}
            </div>
          {/if}
          {#if strokeTool || tool === 'eraser'}
            <label class="flex min-w-[168px] shrink-0 items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5">
              <input
                type="range"
                min={sizing.strokeMin}
                max={sizing.strokeMax}
                step="1"
                class="control-focus h-1.5 w-full accent-emerald-600"
                aria-label={$t.markupEditor.strokeWidth(strokeWidth)}
                bind:value={strokeWidth}
              />
              <span class="w-[86px] shrink-0 text-xs tabular-nums text-zinc-400">{$t.markupEditor.strokeWidth(strokeWidth)}</span>
            </label>
          {/if}
          {#if tool === 'text'}
            <label class="flex min-w-[168px] shrink-0 items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5">
              <input
                type="range"
                min={sizing.textMin}
                max={sizing.textMax}
                step="1"
                class="control-focus h-1.5 w-full accent-emerald-600"
                aria-label={$t.markupEditor.textSize(textSize)}
                bind:value={textSize}
              />
              <span class="w-[86px] shrink-0 text-xs tabular-nums text-zinc-400">{$t.markupEditor.textSize(textSize)}</span>
            </label>
          {/if}
        </div>

        {#if showHelp}
          <div class="rounded-xl border border-zinc-800 bg-zinc-900 p-3" role="note" aria-label={$t.markupEditor.shortcutsTitle}>
            <p class="text-xs font-semibold text-zinc-100">{$t.markupEditor.shortcutsTitle}</p>
            <p class="mt-2 text-xs leading-6 text-zinc-400">{$t.markupEditor.shortcuts}</p>
            <p class="mt-1 text-xs leading-6 text-zinc-400">{$t.markupEditor.historyNote}</p>
          </div>
        {/if}
      </div>

      <div class="relative flex min-h-0 flex-1 items-center justify-center bg-zinc-950 p-3">
        {#if !ready || busy}
          <div
            class="absolute inset-x-0 top-0 z-10 flex items-center justify-center gap-2 border-b border-zinc-800 bg-zinc-900/90 px-3 py-1.5 text-xs text-zinc-300"
            role="status"
          >
            {#if loadError}
              {loadError}
            {:else}
              <span class="spinner" aria-hidden="true"></span>
              {busy ? $t.markupEditor.applying : $t.markupEditor.loading}
            {/if}
          </div>
        {/if}
        <div bind:this={stageEl} class="relative h-full w-full overflow-hidden rounded-lg">
          {#if ready}
            <canvas
              bind:this={canvasEl}
              class="absolute inset-0 h-full w-full touch-none select-none"
              style={`cursor:${cursorStyle}`}
              aria-label={$t.markupEditor.canvasLabel}
              onpointerdown={handlePointerDown}
              onpointermove={handlePointerMove}
              onpointerup={handlePointerUp}
              onpointercancel={handlePointerCancel}
              onlostpointercapture={handleLostCapture}
              oncontextmenu={(event) => event.preventDefault()}
            ></canvas>
            {#if textDraft && textBox}
              <div class="absolute z-10 flex items-start gap-1" style={`left:${textBox.left}px;top:${textBox.top}px`}>
                <input
                  bind:this={textInputEl}
                  bind:value={textDraft.value}
                  type="text"
                  maxlength="80"
                  class="control-focus rounded border border-emerald-400/80 bg-black/75 px-1"
                  style={`color:${color};font:${markupFont(Math.max(16, textBox.size))};line-height:1;min-width:6em;field-sizing:content`}
                  aria-label={$t.markupEditor.textInputLabel}
                  placeholder={$t.markupEditor.textPlaceholder}
                  onkeydown={handleTextKeydown}
                />
                <button
                  type="button"
                  class="mobile-touch-target control-focus flex items-center justify-center rounded-md bg-emerald-600 p-1.5 text-white hover:bg-emerald-700"
                  aria-label={$t.markupEditor.textConfirm}
                  title={$t.markupEditor.textConfirm}
                  onclick={commitTextDraft}
                >
                  <Check size={15} strokeWidth={2} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="mobile-touch-target control-focus flex items-center justify-center rounded-md bg-zinc-800 p-1.5 text-zinc-200"
                  aria-label={$t.markupEditor.textCancel}
                  title={$t.markupEditor.textCancel}
                  onclick={cancelTextDraft}
                >
                  <X size={15} strokeWidth={2} aria-hidden="true" />
                </button>
              </div>
            {/if}
          {/if}
        </div>
      </div>

      <div class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-zinc-800 px-3 py-3">
        <div class={group} role="group" aria-label={$t.markupEditor.zoomLabel}>
          <button type="button" class={iconButton} disabled={!ready || atMinZoom} aria-label={$t.markupEditor.zoomOut} title={$t.markupEditor.zoomOut} onclick={() => zoomBy(1 / 1.25)}>
            <ZoomOut size={15} strokeWidth={1.9} aria-hidden="true" />
          </button>
          <span class="w-[42px] shrink-0 text-center text-xs tabular-nums text-zinc-400" data-testid="markup-zoom">{zoomPercent}%</span>
          <button type="button" class={iconButton} disabled={!ready || atMaxZoom} aria-label={$t.markupEditor.zoomIn} title={$t.markupEditor.zoomIn} onclick={() => zoomBy(1.25)}>
            <ZoomIn size={15} strokeWidth={1.9} aria-hidden="true" />
          </button>
          <button
            type="button"
            class="mobile-touch-target control-focus rounded-md px-1.5 py-1.5 text-xs font-medium tabular-nums text-zinc-300 transition-colors hover:bg-zinc-800"
            disabled={!ready}
            aria-label={$t.markupEditor.zoom100}
            title={$t.markupEditor.zoom100}
            onclick={zoomActual}
          >
            100%
          </button>
          <button type="button" class={iconButton} disabled={!ready} aria-label={$t.markupEditor.fitCanvas} title={$t.markupEditor.fitCanvas} onclick={fitCanvas}>
            <Maximize size={15} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>
        <div class="flex items-center gap-2">
          {#if existing}
            <button
              type="button"
              class="mobile-touch-target control-focus rounded-lg px-3 py-2 text-xs font-medium text-red-300 transition-colors hover:bg-zinc-800 disabled:opacity-50"
              disabled={busy}
              onclick={removeApplied}
            >
              {$t.markupEditor.removeMarkup}
            </button>
          {/if}
          <button
            type="button"
            class="mobile-touch-target control-focus rounded-lg px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800 disabled:opacity-50"
            disabled={busy}
            onclick={requestClose}
          >
            {$t.markupEditor.cancel}
          </button>
          <button
            type="button"
            class="mobile-touch-target control-focus rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!canApply}
            title={hasMarks ? $t.markupEditor.apply : $t.markupEditor.nothingToApply}
            onclick={applyMarkup}
          >
            {$t.markupEditor.apply}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}
