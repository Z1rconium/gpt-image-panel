/**
 * Mask painting document.
 *
 * The data layer is a natural-resolution canvas where any painted alpha counts
 * as "edit this region"; the upstream contract only looks at fully transparent
 * pixels of an exported PNG, so `exportPng` renders the mark color +
 * destination-out and then binarizes alpha to 0/255. Marks are painted in a
 * solid emerald so the editor can draw them straight onto a viewport-sized
 * overlay canvas with `globalAlpha`; the color itself never reaches the export.
 *
 * Coverage is tracked incrementally: every stroke/shape reports its bounding
 * box, `MaskCoverageTracker` keeps a running marked-pixel total, and only
 * undo/redo, import and invert touch full-image arithmetic. Pure helpers live
 * at the top so they can be unit-tested without a canvas.
 */

export const MAX_MASK_FILE_BYTES = 4 * 1024 * 1024;
export const MAX_SMOOTH_PX = 64;

/** Solid fill used for marks; export and coverage only ever look at alpha. */
export const MARK_COLOR = '#10b981';
const MARK_RGB = { r: 16, g: 185, b: 129 } as const;
const MARK_PREVIEW_COLOR = 'rgba(16, 185, 129, 0.45)';

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const ALPHA_CHANNEL_COLOR_TYPES = new Set([4, 6]);
const PALETTE_COLOR_TYPE = 3;

export type MaskTool = 'brush' | 'eraser' | 'rect' | 'lasso' | 'pan';

export type MaskShapeMode = 'add' | 'erase';

export type MaskImportMode = 'replace' | 'merge';

export type MaskPoint = {
  x: number;
  y: number;
};

export type MaskRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type MaskImportErrorCode = 'not-png' | 'no-alpha' | 'size' | 'too-large' | 'decode';

export class MaskImportError extends Error {
  readonly code: MaskImportErrorCode;
  readonly width: number;
  readonly height: number;

  constructor(code: MaskImportErrorCode, size?: { width: number; height: number }) {
    super(code);
    this.name = 'MaskImportError';
    this.code = code;
    this.width = size?.width ?? 0;
    this.height = size?.height ?? 0;
  }
}

/**
 * Binarize the exported mask alpha in place (alpha < 128 becomes 0 = editable,
 * everything else becomes 255 = keep) and return the editable-area ratio.
 */
export function binarizeMaskAlphaData(data: Uint8ClampedArray): number {
  const total = data.length / 4;
  if (!total) return 0;
  let transparent = 0;
  for (let index = 3; index < data.length; index += 4) {
    if (data[index] < 128) {
      data[index] = 0;
      transparent += 1;
    } else {
      data[index] = 255;
    }
  }
  return transparent / total;
}

/** Number of fully marked pixels (alpha >= 128) in the buffer. */
export function countMarked(data: Uint8ClampedArray): number {
  let marked = 0;
  for (let index = 3; index < data.length; index += 4) {
    if (data[index] >= 128) marked += 1;
  }
  return marked;
}

/** Ratio of fully marked pixels on the marks canvas (alpha >= 128). */
export function countMarkedPixels(data: Uint8ClampedArray): number {
  const total = data.length / 4;
  if (!total) return 0;
  return countMarked(data) / total;
}

/**
 * Clamp an edge-smoothing radius (in image pixels) to a sane range for the
 * image size.
 */
export function normalizeSmoothPx(value: number, width: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  const limit = Math.min(MAX_SMOOTH_PX, Math.max(0, Math.floor(width / 8)));
  return Math.min(limit, Math.round(value));
}

/** True when the PNG uses an alpha channel (color type 4/6, or palette + tRNS). */
export function pngHasAlphaChannel(bytes: Uint8Array): boolean {
  if (bytes.length < 26) return false;
  for (let index = 0; index < PNG_SIGNATURE.length; index += 1) {
    if (bytes[index] !== PNG_SIGNATURE[index]) return false;
  }
  const colorType = bytes[25];
  if (ALPHA_CHANNEL_COLOR_TYPES.has(colorType)) return true;
  if (colorType !== PALETTE_COLOR_TYPE) return false;
  for (let index = 8; index + 4 <= bytes.length; index += 1) {
    if (bytes[index] === 0x74 && bytes[index + 1] === 0x52 && bytes[index + 2] === 0x4e && bytes[index + 3] === 0x53) {
      return true;
    }
  }
  return false;
}

/** Clamp a rect to integer canvas bounds; null when it ends up empty. */
export function clampRect(rect: MaskRect, width: number, height: number): MaskRect | null {
  const x = Math.max(0, Math.min(width, Math.floor(rect.x)));
  const y = Math.max(0, Math.min(height, Math.floor(rect.y)));
  const right = Math.max(x, Math.min(width, Math.ceil(rect.x + rect.width)));
  const bottom = Math.max(y, Math.min(height, Math.ceil(rect.y + rect.height)));
  if (right <= x || bottom <= y) return null;
  return { x, y, width: right - x, height: bottom - y };
}

export function containsRect(outer: MaskRect, inner: MaskRect): boolean {
  return (
    inner.x >= outer.x &&
    inner.y >= outer.y &&
    inner.x + inner.width <= outer.x + outer.width &&
    inner.y + inner.height <= outer.y + outer.height
  );
}

export function unionRect(a: MaskRect, b: MaskRect): MaskRect {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return {
    x,
    y,
    width: Math.max(a.x + a.width, b.x + b.width) - x,
    height: Math.max(a.y + a.height, b.y + b.height) - y
  };
}

/** Rects covering `outer` minus `hole` as up to four pairwise-disjoint strips. */
export function subtractRect(outer: MaskRect, hole: MaskRect): MaskRect[] {
  if (outer.width <= 0 || outer.height <= 0) return [];
  const left = Math.max(outer.x, hole.x);
  const top = Math.max(outer.y, hole.y);
  const right = Math.min(outer.x + outer.width, hole.x + hole.width);
  const bottom = Math.min(outer.y + outer.height, hole.y + hole.height);
  if (right <= left || bottom <= top) return [outer];
  const strips: MaskRect[] = [];
  if (top > outer.y) {
    strips.push({ x: outer.x, y: outer.y, width: outer.width, height: top - outer.y });
  }
  if (bottom < outer.y + outer.height) {
    strips.push({ x: outer.x, y: bottom, width: outer.width, height: outer.y + outer.height - bottom });
  }
  if (left > outer.x) {
    strips.push({ x: outer.x, y: top, width: left - outer.x, height: bottom - top });
  }
  if (right < outer.x + outer.width) {
    strips.push({ x: right, y: top, width: outer.x + outer.width - right, height: bottom - top });
  }
  return strips;
}

/** Bounding box of points painted with a stroke of the given size (clamped). */
export function strokeRectFor(
  points: MaskPoint[],
  size: number,
  width: number,
  height: number
): MaskRect | null {
  if (!points.length) return null;
  const pad = Math.ceil(Math.max(1, size) / 2) + 2;
  return pointsRectFor(points, pad, width, height);
}

/** Bounding box of a filled rect/lasso shape's outline points (clamped). */
export function shapeRectFor(
  points: MaskPoint[],
  width: number,
  height: number
): MaskRect | null {
  if (!points.length) return null;
  return pointsRectFor(points, 2, width, height);
}

function pointsRectFor(points: MaskPoint[], pad: number, width: number, height: number): MaskRect | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x);
    maxY = Math.max(maxY, point.y);
  }
  if (!Number.isFinite(minX)) return null;
  return clampRect(
    { x: minX - pad, y: minY - pad, width: maxX - minX + pad * 2, height: maxY - minY + pad * 2 },
    width,
    height
  );
}

/**
 * Incremental marked-pixel accounting. Strokes report their bounding boxes and
 * a region counter before and after painting; the tracker keeps the running
 * total so coverage never needs a full-image read. `countRegion` is injected
 * so this works against a canvas in the editor and a plain buffer in tests.
 */
export class MaskCoverageTracker {
  readonly totalPixels: number;
  private markedPixels = 0;
  private active: { rect: MaskRect; before: number } | null = null;

  constructor(width: number, height: number) {
    this.totalPixels = width * height;
  }

  coverage(): number {
    return this.totalPixels ? this.markedPixels / this.totalPixels : 0;
  }

  /** Snapshot the pre-stroke state of the stroke's first bounding box. */
  beginStroke(rect: MaskRect, countRegion: (rect: MaskRect) => number): void {
    this.active = { rect, before: countRegion(rect) };
  }

  /** Grow the tracked box before painting; only the untouched fringe is read. */
  extendStroke(rect: MaskRect, countRegion: (rect: MaskRect) => number): void {
    const active = this.active;
    if (!active || containsRect(active.rect, rect)) return;
    const grown = unionRect(active.rect, rect);
    for (const fringe of subtractRect(grown, active.rect)) {
      active.before += countRegion(fringe);
    }
    active.rect = grown;
  }

  /** Commit the stroke: fold the bounding box's before/after delta in. */
  endStroke(countRegion: (rect: MaskRect) => number): void {
    const active = this.active;
    this.active = null;
    if (!active) return;
    this.markedPixels += countRegion(active.rect) - active.before;
  }

  /** Drop an in-flight stroke (e.g. a degenerate shape that was not filled). */
  cancelStroke(): void {
    this.active = null;
  }

  /** Absorb a full recount (import, undo/redo replay). */
  resetTo(markedPixels: number): void {
    this.active = null;
    this.markedPixels = markedPixels;
  }

  clear(): void {
    this.active = null;
    this.markedPixels = 0;
  }

  /**
   * Inverting flips every alpha, so exactly the unmarked pixels become marked
   * (alpha >= 128 before <-> alpha < 128 after) and the complement is exact.
   */
  invert(): void {
    this.active = null;
    this.markedPixels = this.totalPixels - this.markedPixels;
  }
}

type StrokeCommand = {
  kind: 'stroke';
  tool: MaskTool;
  size: number;
  points: MaskPoint[];
};

type ShapeCommand = {
  kind: 'rect' | 'lasso';
  mode: MaskShapeMode;
  points: MaskPoint[];
};

type ImportCommand = {
  kind: 'import';
  source: Blob;
  replace: boolean;
};

type ClearCommand = { kind: 'clear' };
type InvertCommand = { kind: 'invert' };
type Command = StrokeCommand | ShapeCommand | ImportCommand | ClearCommand | InvertCommand;

function createCanvas(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function context2d(canvas: HTMLCanvasElement, readFrequently = false): CanvasRenderingContext2D {
  const context = canvas.getContext('2d', readFrequently ? { willReadFrequently: true } : undefined);
  if (!context) throw new Error('Canvas 2D context is unavailable');
  return context;
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new MaskImportError('decode'));
    }, 'image/png');
  });
}

async function decodeImage(file: Blob): Promise<CanvasImageSource & { width: number; height: number }> {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file);
    } catch {
      throw new MaskImportError('decode');
    }
  }

  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new MaskImportError('decode'));
      image.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

export type MaskDocument = ReturnType<typeof createMaskDocument>;

export type MaskMeasurements = {
  width: number;
  height: number;
  coverage: number;
};

/**
 * Measure an exported mask blob (a persisted mask restored from a job): its
 * pixel size and the editable-area ratio, without drawing it into a document.
 */
export async function measureMaskBlob(blob: Blob): Promise<MaskMeasurements> {
  const decoded = await decodeImage(blob);
  const canvas = createCanvas(decoded.width, decoded.height);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new MaskImportError('decode');
  context.drawImage(decoded, 0, 0);
  if (typeof ImageBitmap !== 'undefined' && decoded instanceof ImageBitmap) decoded.close();
  const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
  return {
    width: canvas.width,
    height: canvas.height,
    coverage: binarizeMaskAlphaData(imageData.data)
  };
}

export function createMaskDocument(width: number, height: number) {
  const marks = createCanvas(width, height);
  const context = context2d(marks, true);

  const tracker = new MaskCoverageTracker(width, height);

  let commands: Command[] = [];
  let redoStack: Command[] = [];
  let activeStroke: StrokeCommand | null = null;
  let activeShape: ShapeCommand | null = null;
  let smoothPx = 0;
  let processedStale = false;

  // Lazily allocated: the processed (smoothed) view and its blur helper only
  // exist while edge smoothing is enabled; without it the processed view is
  // the marks canvas itself.
  let processedCanvas: HTMLCanvasElement | null = null;
  let blurCanvas: HTMLCanvasElement | null = null;

  // Single-slot cache of the binarized layer for the most recent import
  // command, so undo/redo replays do not re-decode the stored PNG every time.
  let importLayerCache: { source: Blob; layer: CanvasImageSource } | null = null;

  function strokeContext(target: CanvasRenderingContext2D) {
    target.save();
    target.globalCompositeOperation = 'source-over';
    target.strokeStyle = MARK_COLOR;
    target.lineCap = 'round';
    target.lineJoin = 'round';
  }

  function drawStroke(target: CanvasRenderingContext2D, command: StrokeCommand, fromIndex = 1) {
    const { points, size, tool } = command;
    if (!points.length) return;
    strokeContext(target);
    target.lineWidth = Math.max(1, size);
    target.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over';
    target.beginPath();
    const startIndex = Math.max(0, fromIndex - 1);
    target.moveTo(points[startIndex].x, points[startIndex].y);
    for (let index = startIndex + 1; index < points.length; index += 1) {
      target.lineTo(points[index].x, points[index].y);
    }
    target.stroke();
    target.restore();
  }

  /** Trace a rect/lasso outline into the current path; false when degenerate. */
  function traceShape(target: CanvasRenderingContext2D, command: ShapeCommand): boolean {
    const { points } = command;
    target.beginPath();
    if (command.kind === 'rect') {
      const start = points[0];
      const end = points[points.length - 1];
      if (!start || !end || (start.x === end.x && start.y === end.y)) return false;
      target.rect(
        Math.min(start.x, end.x),
        Math.min(start.y, end.y),
        Math.abs(end.x - start.x),
        Math.abs(end.y - start.y)
      );
      return true;
    }
    if (points.length < 3) return false;
    target.moveTo(points[0].x, points[0].y);
    for (let index = 1; index < points.length; index += 1) {
      target.lineTo(points[index].x, points[index].y);
    }
    target.closePath();
    return true;
  }

  function fillShape(
    target: CanvasRenderingContext2D,
    command: ShapeCommand,
    fillStyle: string = MARK_COLOR
  ) {
    if (!traceShape(target, command)) return false;
    target.save();
    target.globalCompositeOperation = command.mode === 'erase' ? 'destination-out' : 'source-over';
    target.fillStyle = fillStyle;
    target.fill();
    target.restore();
    return true;
  }

  function invertMarks() {
    const scratch = createCanvas(width, height);
    const scratchContext = context2d(scratch);
    scratchContext.fillStyle = MARK_COLOR;
    scratchContext.fillRect(0, 0, width, height);
    scratchContext.globalCompositeOperation = 'destination-out';
    scratchContext.drawImage(marks, 0, 0);

    context.save();
    context.globalCompositeOperation = 'copy';
    context.clearRect(0, 0, width, height);
    context.drawImage(scratch, 0, 0);
    context.restore();

    scratch.width = 0;
    scratch.height = 0;
  }

  function applyCommand(command: Command) {
    switch (command.kind) {
      case 'stroke':
        drawStroke(context, command);
        break;
      case 'rect':
      case 'lasso':
        fillShape(context, command);
        break;
      case 'clear':
        context.clearRect(0, 0, width, height);
        break;
      case 'invert':
        invertMarks();
        break;
      case 'import':
        break;
    }
  }

  function drawImportLayer(layer: CanvasImageSource, replace: boolean) {
    // A replacing import wipes everything before drawing, so undo/redo
    // replay stays a single command and still restores the previous state.
    context.save();
    context.globalCompositeOperation = replace ? 'copy' : 'source-over';
    context.drawImage(layer, 0, 0);
    context.restore();
  }

  async function applyCommandAsync(command: Command) {
    if (command.kind !== 'import') {
      applyCommand(command);
      return;
    }
    drawImportLayer(await preparedImportLayer(command.source), command.replace);
  }

  async function replay() {
    context.save();
    context.globalCompositeOperation = 'copy';
    context.clearRect(0, 0, width, height);
    context.restore();
    for (const command of commands) {
      await applyCommandAsync(command);
    }
  }

  function countRegion(rect: MaskRect): number {
    const region = clampRect(rect, width, height);
    if (!region) return 0;
    const data = context.getImageData(region.x, region.y, region.width, region.height).data;
    return countMarked(data);
  }

  function recountMarkedPixels() {
    tracker.resetTo(countRegion({ x: 0, y: 0, width, height }));
  }

  function invalidateProcessed() {
    processedStale = true;
  }

  function ensureProcessedCanvas(): HTMLCanvasElement {
    if (!processedCanvas) processedCanvas = createCanvas(width, height);
    return processedCanvas;
  }

  function releaseProcessedLayers() {
    if (processedCanvas) {
      processedCanvas.width = 0;
      processedCanvas.height = 0;
      processedCanvas = null;
    }
    if (blurCanvas) {
      blurCanvas.width = 0;
      blurCanvas.height = 0;
      blurCanvas = null;
    }
  }

  // Blur the marks and re-binarize the alpha: the same contour the export
  // produces, so the on-canvas preview never promises a different region.
  // Without smoothing the marks themselves are the processed view and the
  // extra layers are released.
  function repaintProcessed() {
    if (smoothPx <= 0) {
      releaseProcessedLayers();
      processedStale = false;
      return;
    }
    const target = context2d(ensureProcessedCanvas(), true);
    target.save();
    target.globalCompositeOperation = 'copy';
    target.clearRect(0, 0, width, height);
    if (!blurCanvas) blurCanvas = createCanvas(width, height);
    const blur = context2d(blurCanvas, true);
    blur.save();
    blur.globalCompositeOperation = 'copy';
    blur.clearRect(0, 0, width, height);
    blur.filter = `blur(${smoothPx}px)`;
    blur.drawImage(marks, 0, 0);
    blur.filter = 'none';
    blur.restore();
    const imageData = blur.getImageData(0, 0, width, height);
    binarizeMaskAlphaData(imageData.data);
    blur.putImageData(imageData, 0, 0);
    target.drawImage(blurCanvas, 0, 0);
    target.restore();
    processedStale = false;
  }

  /**
   * Edited-area ratio of the processed view. The processed canvas keeps the
   * marks orientation (painted alpha = editable), unlike the exported PNG
   * where editable pixels end up fully transparent.
   */
  function processedEditableRatio(): number {
    if (smoothPx <= 0 || !processedCanvas) return tracker.coverage();
    const data = context2d(processedCanvas, true).getImageData(0, 0, width, height).data;
    return countMarkedPixels(data);
  }

  function releaseImportLayerCache() {
    const cached = importLayerCache;
    importLayerCache = null;
    if (cached && typeof ImageBitmap !== 'undefined' && cached.layer instanceof ImageBitmap) {
      cached.layer.close();
    }
  }

  /** Binarize a decoded import into the mark color and prime the replay cache. */
  async function buildImportLayer(
    source: Blob,
    decoded: CanvasImageSource & { width: number; height: number }
  ): Promise<CanvasImageSource> {
    const layer = createCanvas(width, height);
    const layerContext = context2d(layer, true);
    layerContext.drawImage(decoded, 0, 0);

    const imageData = layerContext.getImageData(0, 0, width, height);
    const data = imageData.data;
    for (let index = 0; index < data.length; index += 4) {
      const editable = data[index + 3] === 0;
      data[index] = MARK_RGB.r;
      data[index + 1] = MARK_RGB.g;
      data[index + 2] = MARK_RGB.b;
      data[index + 3] = editable ? 255 : 0;
    }
    layerContext.putImageData(imageData, 0, 0);

    let cached: CanvasImageSource = layer;
    if (typeof createImageBitmap === 'function') {
      try {
        cached = await createImageBitmap(layer);
      } catch {
        cached = layer;
      }
    }
    releaseImportLayerCache();
    importLayerCache = { source, layer: cached };
    return cached;
  }

  async function preparedImportLayer(source: Blob): Promise<CanvasImageSource> {
    if (importLayerCache && importLayerCache.source === source) return importLayerCache.layer;
    return buildImportLayer(source, await decodeImage(source));
  }

  function coverage(): number {
    return tracker.coverage();
  }

  return {
    width,
    height,
    marks,

    beginStroke(tool: MaskTool, size: number, point: MaskPoint, mode: MaskShapeMode = 'add') {
      if (tool === 'pan') return;
      if (tool === 'rect' || tool === 'lasso') {
        // Shapes stay uncommitted until the pointer is released so an
        // accidental drag can be dismissed and undo has a single entry.
        redoStack = [];
        activeShape = { kind: tool, mode, points: [point] };
        return;
      }
      const command: StrokeCommand = { kind: 'stroke', tool, size, points: [point] };
      commands.push(command);
      redoStack = [];
      activeStroke = command;
      const rect = strokeRectFor(command.points, size, width, height);
      if (rect) tracker.beginStroke(rect, countRegion);
      drawStroke(context, command, 0);
      invalidateProcessed();
    },

    extendStroke(points: MaskPoint[]) {
      if (!points.length) return;
      if (activeShape) {
        if (activeShape.kind === 'rect') {
          activeShape.points = [activeShape.points[0], points[points.length - 1]];
        } else {
          activeShape.points.push(...points);
        }
        return;
      }
      if (!activeStroke) return;
      const fromIndex = activeStroke.points.length;
      activeStroke.points.push(...points);
      const rect = strokeRectFor(points, activeStroke.size, width, height);
      if (rect) tracker.extendStroke(rect, countRegion);
      drawStroke(context, activeStroke, fromIndex);
      invalidateProcessed();
    },

    endStroke() {
      if (activeShape) {
        const command = activeShape;
        activeShape = null;
        const rect = shapeRectFor(command.points, width, height);
        if (rect) tracker.beginStroke(rect, countRegion);
        if (fillShape(context, command)) {
          commands.push(command);
          redoStack = [];
          tracker.endStroke(countRegion);
          invalidateProcessed();
        } else {
          tracker.cancelStroke();
        }
      }
      if (activeStroke) {
        activeStroke = null;
        tracker.endStroke(countRegion);
      }
    },

    canUndo() {
      return commands.length > 0;
    },

    canRedo() {
      return redoStack.length > 0;
    },

    async undo(): Promise<void> {
      const command = commands.pop();
      if (!command) return;
      redoStack.push(command);
      await replay();
      recountMarkedPixels();
      invalidateProcessed();
    },

    async redo(): Promise<void> {
      const command = redoStack.pop();
      if (!command) return;
      commands.push(command);
      await applyCommandAsync(command);
      recountMarkedPixels();
      invalidateProcessed();
    },

    clear() {
      commands.push({ kind: 'clear' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      tracker.clear();
      context.clearRect(0, 0, width, height);
      invalidateProcessed();
    },

    invert() {
      commands.push({ kind: 'invert' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      tracker.invert();
      invertMarks();
      invalidateProcessed();
    },

    async importFromPng(file: Blob, mode: MaskImportMode = 'replace') {
      if (file.size > MAX_MASK_FILE_BYTES) throw new MaskImportError('too-large');
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (bytes.length < 26 || !pngHasAlphaChannel(bytes)) {
        const isPng = PNG_SIGNATURE.every((byte, index) => bytes[index] === byte);
        throw new MaskImportError(isPng ? 'no-alpha' : 'not-png');
      }

      const decoded = await decodeImage(file);
      // Read the size before closing the bitmap: a closed ImageBitmap reports 0x0.
      const decodedWidth = decoded.width;
      const decodedHeight = decoded.height;
      if (decodedWidth !== width || decodedHeight !== height) {
        if (typeof ImageBitmap !== 'undefined' && decoded instanceof ImageBitmap) decoded.close();
        throw new MaskImportError('size', { width: decodedWidth, height: decodedHeight });
      }

      const command: ImportCommand = { kind: 'import', source: file, replace: mode === 'replace' };
      commands.push(command);
      redoStack = [];
      drawImportLayer(await buildImportLayer(file, decoded), command.replace);
      if (typeof ImageBitmap !== 'undefined' && decoded instanceof ImageBitmap) decoded.close();
      recountMarkedPixels();
      invalidateProcessed();
      return coverage();
    },

    /** Change the edge-smoothing radius and return the processed coverage. */
    setSmoothing(px: number) {
      const next = normalizeSmoothPx(px, width);
      if (next === smoothPx && !processedStale) return processedEditableRatio();
      smoothPx = next;
      repaintProcessed();
      return processedEditableRatio();
    },

    /** Repaint the processed view after mark changes; returns its coverage. */
    syncProcessed() {
      if (processedStale) repaintProcessed();
      return processedEditableRatio();
    },

    processedFresh() {
      return smoothPx <= 0 || !processedStale;
    },

    get smoothPx() {
      return smoothPx;
    },

    /** The processed view; identical to `marks` unless smoothing is enabled. */
    get processed(): HTMLCanvasElement {
      return smoothPx > 0 && processedCanvas ? processedCanvas : marks;
    },

    coverage,

    hasActiveShape() {
      return activeShape !== null;
    },

    /** Draw the in-progress rect/lasso into a context in image coordinates. */
    drawActiveShapePreview(target: CanvasRenderingContext2D) {
      if (!activeShape) return;
      fillShape(target, activeShape, activeShape.mode === 'erase' ? '#000' : MARK_PREVIEW_COLOR);
    },

    async exportPng(options: { smoothing?: number } = {}) {
      const smoothing = normalizeSmoothPx(options.smoothing ?? smoothPx, width);
      const canvas = createCanvas(width, height);
      const exportContext = context2d(canvas, true);
      exportContext.fillStyle = '#000';
      exportContext.fillRect(0, 0, width, height);
      exportContext.globalCompositeOperation = 'destination-out';

      // The upstream contract only edits alpha == 0 pixels, so a soft gradient
      // cannot survive the round trip. Blurring before binarizing only smooths
      // the contour (removes pointer jaggies) while keeping the region binary —
      // the exact rule the on-canvas preview and coverage readout use.
      if (smoothing > 0) {
        const blurred = createCanvas(width, height);
        const blurredContext = context2d(blurred, true);
        blurredContext.filter = `blur(${smoothing}px)`;
        blurredContext.drawImage(marks, 0, 0);
        exportContext.drawImage(blurred, 0, 0);
      } else {
        exportContext.drawImage(marks, 0, 0);
      }
      exportContext.globalCompositeOperation = 'source-over';

      const imageData = exportContext.getImageData(0, 0, width, height);
      const ratio = binarizeMaskAlphaData(imageData.data);
      exportContext.putImageData(imageData, 0, 0);

      return { blob: await canvasBlob(canvas), coverage: ratio };
    },

    dispose() {
      commands = [];
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      tracker.resetTo(0);
      releaseImportLayerCache();
      releaseProcessedLayers();
      marks.width = 0;
      marks.height = 0;
    }
  };
}
