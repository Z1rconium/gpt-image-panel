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

/**
 * Above this share of the frame, a dirty-region repaint of the smoothed view
 * costs more bookkeeping than it saves, so the full-frame path is used instead.
 */
const PROCESSED_REGION_MAX_RATIO = 0.25;

/** Solid fill used for marks; export and coverage only ever look at alpha. */
export const MARK_COLOR = '#10b981';
const MARK_RGB = { r: 16, g: 185, b: 129 } as const;
const MARK_PREVIEW_COLOR = 'rgba(16, 185, 129, 0.45)';

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const ALPHA_CHANNEL_COLOR_TYPES = new Set([4, 6]);
const PALETTE_COLOR_TYPE = 3;

export type MaskTool = 'brush' | 'eraser' | 'rect' | 'ellipse' | 'lasso' | 'pan';

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

/**
 * Pixel size straight from the IHDR chunk (bytes 16-19 width, 20-23 height,
 * big-endian), so a caller that only needs dimensions never has to decode the
 * image through a canvas. Null for anything that isn't a PNG with a complete
 * IHDR header.
 */
export function readPngSize(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.length < 24) return null;
  for (let index = 0; index < PNG_SIGNATURE.length; index += 1) {
    if (bytes[index] !== PNG_SIGNATURE[index]) return null;
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(16, false);
  const height = view.getUint32(20, false);
  if (width <= 0 || height <= 0) return null;
  return { width, height };
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

/** Grow a rect by `pad` on every side, clamped to the canvas; null if empty. */
export function expandRect(
  rect: MaskRect,
  pad: number,
  width: number,
  height: number
): MaskRect | null {
  return clampRect(
    {
      x: rect.x - pad,
      y: rect.y - pad,
      width: rect.width + pad * 2,
      height: rect.height + pad * 2
    },
    width,
    height
  );
}

export function rectArea(rect: MaskRect): number {
  return Math.max(0, rect.width) * Math.max(0, rect.height);
}

/** True when the rect is unusable or spans the whole canvas. */
export function rectCoversAll(rect: MaskRect | null, width: number, height: number): boolean {
  if (!rect) return true;
  return (
    rect.x <= 0 && rect.y <= 0 && rect.x + rect.width >= width && rect.y + rect.height >= height
  );
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

  /**
   * Coverage including the in-flight stroke's painted-so-far delta. `coverage()`
   * only changes when a stroke is committed, so the editor needs this to show
   * the number move while the user is still drawing.
   */
  preview(countRegion: (rect: MaskRect) => number): number {
    const active = this.active;
    if (!active || !this.totalPixels) return this.coverage();
    return (this.markedPixels + (countRegion(active.rect) - active.before)) / this.totalPixels;
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
  kind: 'rect' | 'ellipse' | 'lasso';
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

/**
 * Bookkeeping for undo checkpoints: which command-list lengths have a
 * snapshot, which one is nearest for a given undo target, and eviction once
 * the cap is reached. Kept free of canvas access (generic over the snapshot
 * type `T`) so it can be unit-tested directly; `createMaskDocument` supplies
 * the actual pixel snapshot/restore.
 */
export class MaskCheckpointer<T> {
  private readonly interval: number;
  private readonly maxCheckpoints: number;
  private checkpoints: { afterIndex: number; snapshot: T }[] = [];

  constructor(interval: number, maxCheckpoints: number) {
    this.interval = Math.max(1, interval);
    this.maxCheckpoints = Math.max(1, maxCheckpoints);
  }

  /** Record a snapshot for `length` if it lands on the interval and isn't already captured. */
  record(length: number, snapshot: () => T): void {
    if (length === 0 || length % this.interval !== 0) return;
    const last = this.checkpoints[this.checkpoints.length - 1];
    if (last && last.afterIndex === length) return;
    this.checkpoints.push({ afterIndex: length, snapshot: snapshot() });
    if (this.checkpoints.length > this.maxCheckpoints) this.checkpoints.shift();
  }

  /** Drop checkpoints past `length` — their commands were just undone. */
  pruneAbove(length: number): void {
    while (this.checkpoints.length && this.checkpoints[this.checkpoints.length - 1].afterIndex > length) {
      this.checkpoints.pop();
    }
  }

  /** The latest checkpoint at or before `length`, after pruning stale ones. */
  nearestAtOrBelow(length: number): { afterIndex: number; snapshot: T } | null {
    this.pruneAbove(length);
    return this.checkpoints[this.checkpoints.length - 1] ?? null;
  }

  clear(): void {
    this.checkpoints = [];
  }
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

  // Repaint bookkeeping for the processed view: the union of regions whose
  // marks changed since the last repaint, plus the running marked-pixel total
  // so `processedEditableRatio()` never needs a second full-frame read.
  let processedDirty: MaskRect | null = null;
  let processedMarked = 0;

  // Single-slot cache of the binarized layer for the most recent import
  // command, so undo/redo replays do not re-decode the stored PNG every time.
  let importLayerCache: { source: Blob; layer: CanvasImageSource } | null = null;

  // Undo checkpoints: a full-alpha snapshot every CHECKPOINT_INTERVAL
  // committed commands, so undo replays only the tail since the nearest one
  // instead of the whole history. Marks are always solid MARK_COLOR wherever
  // painted (strokes/shapes/import all fill that one color), so the alpha
  // plane alone is enough to reconstruct the canvas exactly. Capped at
  // MAX_CHECKPOINTS entries; undoing past the oldest one falls back to a
  // full replay from empty, same as before this existed.
  const CHECKPOINT_INTERVAL = 8;
  const MAX_CHECKPOINTS = 4;
  const checkpointer = new MaskCheckpointer<Uint8ClampedArray>(CHECKPOINT_INTERVAL, MAX_CHECKPOINTS);

  function captureAlphaSnapshot(): Uint8ClampedArray {
    const { data } = context.getImageData(0, 0, width, height);
    const alpha = new Uint8ClampedArray(width * height);
    for (let index = 0, pixel = 3; index < alpha.length; index += 1, pixel += 4) {
      alpha[index] = data[pixel];
    }
    return alpha;
  }

  function restoreAlphaSnapshot(alpha: Uint8ClampedArray) {
    const imageData = context.createImageData(width, height);
    const data = imageData.data;
    for (let index = 0, pixel = 0; index < alpha.length; index += 1, pixel += 4) {
      const value = alpha[index];
      if (!value) continue;
      data[pixel] = MARK_RGB.r;
      data[pixel + 1] = MARK_RGB.g;
      data[pixel + 2] = MARK_RGB.b;
      data[pixel + 3] = value;
    }
    context.save();
    context.globalCompositeOperation = 'copy';
    context.putImageData(imageData, 0, 0);
    context.restore();
  }

  function maybeCheckpoint() {
    checkpointer.record(commands.length, captureAlphaSnapshot);
  }

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

  /** Trace a rect/ellipse/lasso outline into the current path; false when degenerate. */
  function traceShape(target: CanvasRenderingContext2D, command: ShapeCommand): boolean {
    const { points } = command;
    target.beginPath();
    if (command.kind === 'rect' || command.kind === 'ellipse') {
      const start = points[0];
      const end = points[points.length - 1];
      if (!start || !end || (start.x === end.x && start.y === end.y)) return false;
      const x = Math.min(start.x, end.x);
      const y = Math.min(start.y, end.y);
      const rectWidth = Math.abs(end.x - start.x);
      const rectHeight = Math.abs(end.y - start.y);
      if (command.kind === 'rect') {
        target.rect(x, y, rectWidth, rectHeight);
      } else {
        // Ellipse inscribed in the dragged box, matching the rect tool's corners.
        target.ellipse(
          x + rectWidth / 2,
          y + rectHeight / 2,
          rectWidth / 2,
          rectHeight / 2,
          0,
          0,
          Math.PI * 2
        );
      }
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
      case 'ellipse':
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

  /** Rebuild the canvas up to `targetLength` commands, from the nearest checkpoint. */
  async function replayFrom(targetLength: number) {
    const checkpoint = checkpointer.nearestAtOrBelow(targetLength);
    if (checkpoint) {
      restoreAlphaSnapshot(checkpoint.snapshot);
    } else {
      context.save();
      context.globalCompositeOperation = 'copy';
      context.clearRect(0, 0, width, height);
      context.restore();
    }
    const startIndex = checkpoint ? checkpoint.afterIndex : 0;
    for (let index = startIndex; index < targetLength; index += 1) {
      await applyCommandAsync(commands[index]);
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

  /**
   * Mark the processed view stale. Pass the region whose marks changed to let
   * the next repaint touch only that area; omit it (or pass null) when the
   * change is unbounded (clear/invert/import/undo replay, smoothing radius).
   */
  function invalidateProcessed(rect: MaskRect | null = null) {
    processedStale = true;
    if (smoothPx <= 0) return;
    if (!rect) {
      processedDirty = { x: 0, y: 0, width, height };
      return;
    }
    processedDirty = processedDirty ? unionRect(processedDirty, rect) : rect;
  }

  function ensureProcessedCanvas(): HTMLCanvasElement {
    if (!processedCanvas) processedCanvas = createCanvas(width, height);
    return processedCanvas;
  }

  function ensureBlurCanvas(): HTMLCanvasElement {
    if (!blurCanvas) blurCanvas = createCanvas(width, height);
    return blurCanvas;
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
    processedMarked = 0;
    processedDirty = null;
  }

  /** Marked pixels of the processed view inside `rect` (clamped). */
  function countProcessedRegion(rect: MaskRect): number {
    const region = clampRect(rect, width, height);
    if (!region || !processedCanvas) return 0;
    const data = context2d(processedCanvas, true).getImageData(
      region.x,
      region.y,
      region.width,
      region.height
    ).data;
    return countMarked(data);
  }

  /** Full-frame repaint: blur + binarize every pixel and recount the total. */
  function repaintProcessedFull(target: CanvasRenderingContext2D, blur: CanvasRenderingContext2D) {
    blur.save();
    blur.globalCompositeOperation = 'copy';
    blur.clearRect(0, 0, width, height);
    blur.filter = `blur(${smoothPx}px)`;
    blur.drawImage(marks, 0, 0);
    blur.filter = 'none';
    blur.restore();

    const imageData = blur.getImageData(0, 0, width, height);
    const transparentRatio = binarizeMaskAlphaData(imageData.data);
    blur.putImageData(imageData, 0, 0);

    target.save();
    target.globalCompositeOperation = 'copy';
    target.clearRect(0, 0, width, height);
    target.drawImage(blurCanvas as HTMLCanvasElement, 0, 0);
    target.restore();

    const total = width * height;
    processedMarked = Math.round((1 - transparentRatio) * total);
  }

  /**
   * Region repaint: blur a padded input area, binarize the expanded output
   * region, and fold that region's marked-pixel delta into the running total.
   *
   * Both partial draws below explicitly `clearRect` their own sub-rectangle
   * and rely on the default `source-over` compositing rather than `copy`:
   * per the Canvas 2D Porter-Duff "copy" operator (Co = Cs, Ao = As), setting
   * `globalCompositeOperation = 'copy'` and then drawing into only part of a
   * canvas clears the *entire* canvas to transparent outside the drawn area,
   * not just the sub-rectangle being updated — it would silently erase every
   * previously smoothed stroke elsewhere on the canvas on every incremental
   * repaint. `repaintProcessedFull` gets away with `copy` because it always
   * draws the whole canvas, so there is no "outside" to lose.
   */
  function repaintProcessedRegion(
    target: CanvasRenderingContext2D,
    blur: CanvasRenderingContext2D,
    output: MaskRect
  ) {
    // CSS `blur(<length>)` uses the length as the Gaussian's standard
    // deviation, whose significant support reaches well past the nominal
    // radius (~3 sigma covers >99% of the kernel's mass), so the padding
    // has to be a multiple of smoothPx rather than a small constant offset
    // or the inner box would be computed from a clipped, under-blurred edge.
    const padding = Math.ceil(smoothPx * 3) + 2;
    // Keep padding around the output region so the cropped input has the same
    // blur samples as a full-frame draw.
    const input = expandRect(output, padding, width, height);
    if (!input) return;
    const before = countProcessedRegion(output);

    blur.save();
    blur.clearRect(input.x, input.y, input.width, input.height);
    blur.filter = `blur(${smoothPx}px)`;
    blur.drawImage(
      marks,
      input.x,
      input.y,
      input.width,
      input.height,
      input.x,
      input.y,
      input.width,
      input.height
    );
    blur.filter = 'none';
    blur.restore();

    const imageData = blur.getImageData(output.x, output.y, output.width, output.height);
    const transparentRatio = binarizeMaskAlphaData(imageData.data);
    blur.putImageData(imageData, output.x, output.y);

    target.save();
    target.clearRect(output.x, output.y, output.width, output.height);
    target.drawImage(
      blurCanvas as HTMLCanvasElement,
      output.x,
      output.y,
      output.width,
      output.height,
      output.x,
      output.y,
      output.width,
      output.height
    );
    target.restore();

    // Reuse the ratio the binarize pass already computed instead of reading
    // the just-drawn pixels back a second time.
    const after = Math.round((1 - transparentRatio) * (output.width * output.height));
    processedMarked += after - before;
  }

  // Blur the marks and re-binarize the alpha: the same contour the export
  // produces, so the on-canvas preview never promises a different region.
  // Without smoothing the marks themselves are the processed view and the
  // extra layers are released. A repaint only touches the dirty region unless
  // it spans too much of the frame to be worth the bookkeeping (or is unknown).
  function repaintProcessed() {
    if (smoothPx <= 0) {
      releaseProcessedLayers();
      processedStale = false;
      return;
    }
    const target = context2d(ensureProcessedCanvas(), true);
    const blur = context2d(ensureBlurCanvas(), true);
    const dirty = processedDirty;
    const full: MaskRect = { x: 0, y: 0, width, height };
    const processedRegion = dirty && expandRect(dirty, Math.ceil(smoothPx * 3) + 2, width, height);
    if (
      !processedRegion ||
      rectCoversAll(processedRegion, width, height) ||
      rectArea(processedRegion) > rectArea(full) * PROCESSED_REGION_MAX_RATIO
    ) {
      repaintProcessedFull(target, blur);
    } else {
      repaintProcessedRegion(target, blur, processedRegion);
    }
    processedDirty = null;
    processedStale = false;
  }

  /**
   * Edited-area ratio of the processed view. The processed canvas keeps the
   * marks orientation (painted alpha = editable), unlike the exported PNG
   * where editable pixels end up fully transparent. The count is maintained by
   * the repaint above, so this never reads pixels back.
   */
  function processedEditableRatio(): number {
    if (smoothPx <= 0 || !processedCanvas) return tracker.coverage();
    const total = width * height;
    return total ? processedMarked / total : 0;
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
      if (tool === 'rect' || tool === 'ellipse' || tool === 'lasso') {
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
      invalidateProcessed(rect);
    },

    extendStroke(points: MaskPoint[]) {
      if (!points.length) return;
      if (activeShape) {
        // A shape only touches the marks canvas on release, so there is no
        // processed repaint to schedule while dragging.
        if (activeShape.kind === 'rect' || activeShape.kind === 'ellipse') {
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
      invalidateProcessed(rect);
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
          invalidateProcessed(rect);
          maybeCheckpoint();
        } else {
          tracker.cancelStroke();
        }
      }
      if (activeStroke) {
        activeStroke = null;
        tracker.endStroke(countRegion);
        maybeCheckpoint();
      }
    },

    /** Drop the in-flight rect/ellipse/lasso drag without committing it. */
    cancelActiveShape() {
      if (!activeShape) return false;
      activeShape = null;
      return true;
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
      await replayFrom(commands.length);
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
      maybeCheckpoint();
    },

    clear() {
      commands.push({ kind: 'clear' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      tracker.clear();
      context.clearRect(0, 0, width, height);
      invalidateProcessed();
      maybeCheckpoint();
    },

    invert() {
      commands.push({ kind: 'invert' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      tracker.invert();
      invertMarks();
      invalidateProcessed();
      maybeCheckpoint();
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
      maybeCheckpoint();
      return coverage();
    },

    /** Change the edge-smoothing radius and return the processed coverage. */
    setSmoothing(px: number) {
      const next = normalizeSmoothPx(px, width);
      if (next === smoothPx && !processedStale) return processedEditableRatio();
      // A different radius changes every pixel's blur input, so the whole
      // frame has to be rebuilt regardless of what was dirty.
      if (next !== smoothPx) processedDirty = { x: 0, y: 0, width, height };
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

    /** Live coverage of the committed marks plus the stroke in progress. */
    liveCoverage() {
      return tracker.preview(countRegion);
    },

    hasActiveShape() {
      return activeShape !== null;
    },

    /** Draw the in-progress rect/ellipse/lasso into a context in image coordinates. */
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
      checkpointer.clear();
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
