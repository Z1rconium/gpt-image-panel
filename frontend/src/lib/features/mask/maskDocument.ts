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
 * Two binary mirrors shadow the canvas, one byte per pixel, so hot paths never
 * need a full-frame read:
 *
 * - `mirror` is the mark set M (alpha >= 128). It is maintained from the same
 *   read-backs that count pixels — every write to the marks canvas covers a
 *   known bounding box, so reading that box back keeps the mirror exact — and
 *   rebuilt wholesale after undo/redo/import/invert/clear.
 * - `protectedMirror` is set P, the pixels the user explicitly erased. Gap
 *   filling must never restore them (invariant I2), while painting over one
 *   takes it out again.
 *
 * Coverage is tracked incrementally: every stroke/shape folds a region's
 * before/after counts in, so a frame of live coverage costs O(segment) instead
 * of O(stroke bounding box); the mirror answers the "before" count without
 * touching the GPU at all (P1). Only undo/redo, import and invert still do
 * full-image arithmetic.
 *
 * Pure helpers live at the top so they can be unit-tested without a canvas.
 */

import {
  MARK_ALPHA_THRESHOLD,
  MAX_MASK_FILE_BYTES,
  MaskImportError,
  binarizeMaskAlphaData,
  context2d,
  createCanvas,
  decodeImage,
  pngHasAlphaChannel,
  readPngSize
} from './maskImage';
import type { EdgeMap } from './maskSnap';
import type { MaskRegionParams, MaskRegionRequest } from './maskRegion';

// Shared with the workspace's mask restore path, which must not pull in the
// painting document (see maskImage.ts); re-exported so importers keep one home.
export {
  MAX_MASK_FILE_BYTES,
  MARK_ALPHA_THRESHOLD,
  MaskImportError,
  binarizeMaskAlphaData,
  countMarked,
  countMarkedPixels,
  measureMaskBlob,
  pngHasAlphaChannel,
  readPngSize,
  type MaskImportErrorCode,
  type MaskMeasurements
} from './maskImage';

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

export type MaskTool = 'brush' | 'eraser' | 'rect' | 'ellipse' | 'lasso' | 'pan';

export type MaskShapeMode = 'add' | 'erase';

export type MaskImportMode = 'replace' | 'merge';

function isJpeg(bytes: Uint8Array): boolean {
  return bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
}

function isWebp(bytes: Uint8Array): boolean {
  return bytes.length >= 12 &&
    bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 &&
    bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50;
}

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

/**
 * Binarize one region of the smoothed view in place and return how many of its
 * pixels end up editable.
 *
 * The rule is `(blurred >= 128 ∪ M) \ P`: the blur's 50% threshold *union* the
 * marks mirror, minus the protection set. Union rather than threshold alone is
 * what makes smoothing monotone (I1) — a blur narrows a thin stroke's peak
 * below the threshold and used to delete it outright — while the protection set
 * keeps an explicit erase from being smoothed back over (I2).
 *
 * `data` holds one `ImageData` for `region`; `mirror`/`protect` are full-frame
 * 0/1 bitmaps indexed with `width`. When `target` is given it receives the same
 * 0/1 result, which is how the caller keeps its processed-view mirror in step
 * without a second pass.
 */
export function binarizeUnion(
  data: Uint8ClampedArray,
  region: MaskRect,
  options: {
    width: number;
    mirror: Uint8Array;
    protect?: Uint8Array | null;
    target?: Uint8Array | null;
  }
): number {
  const { width, mirror, protect, target } = options;
  let marked = 0;
  for (let row = 0; row < region.height; row += 1) {
    const mirrorRow = (region.y + row) * width + region.x;
    const pixelRow = row * region.width * 4;
    for (let column = 0; column < region.width; column += 1) {
      const index = mirrorRow + column;
      const pixel = pixelRow + column * 4 + 3;
      const editable =
        (data[pixel] >= MARK_ALPHA_THRESHOLD || mirror[index] === 1) &&
        (!protect || protect[index] !== 1);
      const value = editable ? 1 : 0;
      data[pixel] = editable ? 255 : 0;
      if (target) target[index] = value;
      if (value) marked += 1;
    }
  }
  return marked;
}

/** Marked pixels of a 0/1 bitmap inside `rect` (clamped by the caller). */
export function countMirrorRegion(source: Uint8Array, rect: MaskRect, width: number): number {
  let marked = 0;
  for (let row = 0; row < rect.height; row += 1) {
    const start = (rect.y + row) * width + rect.x;
    for (let column = 0; column < rect.width; column += 1) {
      if (source[start + column]) marked += 1;
    }
  }
  return marked;
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
 * Incremental marked-pixel accounting.
 *
 * Callers report one region at a time as a before/after pair: for a stroke that
 * is the segment just painted (read before and after the draw), for a shape the
 * whole filled bounding box. Because only that region can have changed, the
 * difference is exact and the running total never needs a full-image read — not
 * even for the in-flight stroke, whose segments are folded in as they happen.
 */
export class MaskCoverageTracker {
  readonly totalPixels: number;
  private markedPixels = 0;

  constructor(width: number, height: number) {
    this.totalPixels = width * height;
  }

  coverage(): number {
    return this.totalPixels ? this.markedPixels / this.totalPixels : 0;
  }

  markedCount(): number {
    return this.markedPixels;
  }

  /** Fold one region's before/after counts in. */
  applyDelta(before: number, after: number): void {
    this.markedPixels += after - before;
  }

  /** Absorb a full recount (import, undo/redo replay, mirror rebuild). */
  resetTo(markedPixels: number): void {
    this.markedPixels = markedPixels;
  }

  clear(): void {
    this.markedPixels = 0;
  }

  /**
   * Inverting flips every alpha, so exactly the unmarked pixels become marked
   * (alpha >= 128 before <-> alpha < 128 after) and the complement is exact.
   */
  invert(): void {
    this.markedPixels = this.totalPixels - this.markedPixels;
  }
}

type StrokeCommand = {
  kind: 'stroke';
  tool: MaskTool;
  size: number;
  points: MaskPoint[];
  // Cumulative point-count boundary after each incremental draw call (the
  // dab from beginStroke, then one per extendStroke). Replaying through these
  // boundaries with the same fromIndex/toIndex pairs reproduces the exact
  // sequence of overlapping round-capped strokes live painting drew, instead
  // of tracing every point as one continuous path (which joins corners
  // instead of capping them and lands a few antialiased edge pixels
  // differently) — see plan Phase 4, "按段精确重放".
  segments: number[];
};

/** One rectangle's pixels as they stood immediately before a command touched them. */
type PixelFragment = {
  rect: MaskRect;
  alpha: Uint8ClampedArray;
  protect: Uint8Array | null;
};

/**
 * Everything needed to undo one stroke/shape command without a full replay:
 * its pre-command pixels (as one fragment per incremental draw, restored in
 * reverse so overlapping segments end up with whatever was under the
 * *earliest* one), and the net change in marked-pixel count so the coverage
 * tracker can be adjusted in O(1) instead of recounted.
 */
type CommandPatch = {
  fragments: PixelFragment[];
  rect: MaskRect;
  markedDelta: number;
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

/** Stroke/shape commands are the only ones an undo patch is ever recorded for (plan P3/P4 scope). */
function isPatchable(command: Command): command is StrokeCommand | ShapeCommand {
  return (
    command.kind === 'stroke' ||
    command.kind === 'rect' ||
    command.kind === 'ellipse' ||
    command.kind === 'lasso'
  );
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new MaskImportError('decode'));
    }, 'image/png');
  });
}

/** Release a drawable layer produced by the region worker. */
export function releaseImageSource(source: CanvasImageSource | null | undefined): void {
  if (source && typeof ImageBitmap !== 'undefined' && source instanceof ImageBitmap) source.close();
}

export type MaskDocument = ReturnType<typeof createMaskDocument>;

/**
 * Bookkeeping for undo checkpoints: which command-list lengths have a
 * snapshot, which one is nearest for a given undo target, and eviction once
 * the cap is reached. Kept free of canvas access (generic over the snapshot
 * type `T`) so it can be unit-tested directly; `createMaskDocument` supplies
 * the actual pixel snapshot/restore.
 */
/**
 * Small set of dirty rectangles standing in for a single ever-growing
 * bounding box (plan Phase 4, P3b).
 *
 * Two edits in opposite corners of the canvas used to union into one bbox
 * spanning almost the whole frame, which then tripped the full-repaint
 * fallback below. Keeping a handful of separate rects — merging only when two
 * of them are already close enough that the union wastes little area — keeps
 * unrelated edits from paying for each other's repaint. Past `maxRegions` it
 * gives up and degrades to the old single-union behaviour.
 */
export class DirtyRegionList {
  private regions: MaskRect[] = [];
  private full = false;

  constructor(
    private readonly maxRegions = 32,
    private readonly mergeWaste = 1.5
  ) {}

  /** The whole canvas is dirty (clear/invert/import/undo-replay/radius change). */
  markFull(): void {
    this.full = true;
    this.regions = [];
  }

  /** Merge `rect` into an existing region when that costs little extra area. */
  markRect(rect: MaskRect): void {
    if (this.full) return;
    for (let index = 0; index < this.regions.length; index += 1) {
      const existing = this.regions[index];
      const merged = unionRect(existing, rect);
      if (rectArea(merged) <= (rectArea(existing) + rectArea(rect)) * this.mergeWaste) {
        this.regions[index] = merged;
        return;
      }
    }
    this.regions.push(rect);
    if (this.regions.length > this.maxRegions) {
      this.regions = [this.regions.reduce(unionRect)];
    }
  }

  get isFull(): boolean {
    return this.full;
  }

  get isEmpty(): boolean {
    return !this.full && this.regions.length === 0;
  }

  /** The current regions; meaningless (and empty) while `isFull` is true. */
  get list(): MaskRect[] {
    return this.regions;
  }

  clear(): void {
    this.full = false;
    this.regions = [];
  }
}

/**
 * Byte-budgeted store of undo patches, keyed by the command-list length right
 * after the command they belong to was committed (its "afterIndex", same
 * convention as `MaskCheckpointer`).
 *
 * Unlike the checkpointer this is a random-access map, not a stack: undoing
 * several commands in a row looks a patch up at each afterIndex in turn, and
 * redo re-finds the same entry rather than recomputing it (see
 * `dropAbove`). Once the total exceeds the byte budget the oldest patches —
 * the ones farthest back in the command history, so least likely to be the
 * next undo — are evicted first; an undo that misses falls back to the
 * checkpoint-and-replay path, same as before this existed.
 */
export class MaskUndoPatches<T> {
  private readonly budgetBytes: number;
  private order: number[] = [];
  private patches = new Map<number, { bytes: number; data: T }>();
  private totalBytes = 0;

  constructor(budgetBytes: number) {
    this.budgetBytes = Math.max(0, budgetBytes);
  }

  record(afterIndex: number, bytes: number, data: T): void {
    if (this.patches.has(afterIndex)) this.dropExact(afterIndex);
    this.patches.set(afterIndex, { bytes, data });
    this.order.push(afterIndex);
    this.totalBytes += bytes;
    this.evict();
  }

  private evict(): void {
    while (this.totalBytes > this.budgetBytes && this.order.length > 0) {
      const oldest = this.order.shift();
      if (oldest === undefined) break;
      const entry = this.patches.get(oldest);
      if (entry) {
        this.totalBytes -= entry.bytes;
        this.patches.delete(oldest);
      }
    }
  }

  private dropExact(afterIndex: number): void {
    const entry = this.patches.get(afterIndex);
    if (!entry) return;
    this.totalBytes -= entry.bytes;
    this.patches.delete(afterIndex);
    this.order = this.order.filter((index) => index !== afterIndex);
  }

  get(afterIndex: number): T | null {
    return this.patches.get(afterIndex)?.data ?? null;
  }

  /** Drop patches whose command was only reachable through a just-cleared redo stack. */
  dropAbove(afterIndex: number): void {
    while (this.order.length && this.order[this.order.length - 1] > afterIndex) {
      const index = this.order.pop();
      if (index === undefined) break;
      const entry = this.patches.get(index);
      if (entry) {
        this.totalBytes -= entry.bytes;
        this.patches.delete(index);
      }
    }
  }

  clear(): void {
    this.order = [];
    this.patches.clear();
    this.totalBytes = 0;
  }

  get usedBytes(): number {
    return this.totalBytes;
  }
}

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

  // Binary mirrors (see the module header). `mirror` shadows the marks canvas,
  // `protectedMirror` is allocated the first time the user erases something,
  // and `processedMirror` shadows the smoothed view while it exists.
  const mirror = new Uint8Array(width * height);
  let protectedMirror: Uint8Array | null = null;
  let processedMirror: Uint8Array | null = null;

  let commands: Command[] = [];
  let redoStack: Command[] = [];
  let activeStroke: StrokeCommand | null = null;
  let activeShape: ShapeCommand | null = null;

  // Undo patch being assembled for the in-flight stroke: one fragment per
  // incremental draw (captured right before it), their union rect, and the
  // marked-pixel count at the moment the stroke began (its patch's
  // markedDelta is this minus the tracker's count when the stroke ends).
  let patchFragments: PixelFragment[] = [];
  let patchRect: MaskRect | null = null;
  let patchStartMarked = 0;
  let smoothPx = 0;
  let processedStale = false;

  // Region gap filling. `regionVersion` counts every change to the inputs (M, P,
  // the processed view A, or the parameters); a worker result carrying an older
  // version is dropped. `autoCount`/`autoSource` hold the newest accepted
  // additions layer D.
  let regionParams: MaskRegionParams | null = null;
  let regionVersion = 0;
  let appliedRegionVersion = 0;
  let autoCount = 0;
  let autoSource: CanvasImageSource | null = null;
  let autoBbox: MaskRect | null = null;

  // Lazily allocated: the processed (smoothed) view and its blur helper only
  // exist while edge smoothing is enabled; without it the processed view is
  // the marks canvas itself.
  let processedCanvas: HTMLCanvasElement | null = null;
  let blurCanvas: HTMLCanvasElement | null = null;

  // Repaint bookkeeping for the processed view: the union of regions whose
  // marks changed since the last repaint, plus the running marked-pixel total
  // so `processedEditableRatio()` never needs a second full-frame read.
  const processedDirty = new DirtyRegionList();
  let processedMarked = 0;

  // Single-slot cache of the binarized layer for the most recent import
  // command, so undo/redo replays do not re-decode the stored PNG every time.
  let importLayerCache: { source: Blob; layer: CanvasImageSource; alphaMask: boolean } | null = null;

  // Scratch canvas for rasterizing eraser geometry into the protection set. It
  // grows to the largest eraser bounding box seen and is reused.
  let protectCanvas: HTMLCanvasElement | null = null;

  // Undo checkpoints: a full-alpha snapshot every CHECKPOINT_INTERVAL
  // committed commands, so a patch-less undo (see MaskUndoPatches below)
  // replays only the tail since the nearest one instead of the whole history.
  // Marks are always solid MARK_COLOR wherever painted (strokes/shapes/import
  // all fill that one color), so the alpha plane alone is enough to
  // reconstruct the canvas exactly. Capped at MAX_CHECKPOINTS entries; undoing
  // past the oldest one falls back to a full replay from empty. Now that most
  // undos are served by a patch instead (P4), this exists only as the
  // fallback for clear/invert/import and for strokes whose patch fell out of
  // the byte budget, so it can afford to be smaller than before Phase 4.
  const CHECKPOINT_INTERVAL = 8;
  const MAX_CHECKPOINTS = 2;
  const checkpointer = new MaskCheckpointer<Uint8Array>(CHECKPOINT_INTERVAL, MAX_CHECKPOINTS);

  // Undo patches (plan Phase 4 / P4): one entry per committed stroke/shape
  // command, keyed by the command-list length right after it was pushed.
  // Clear/invert/import never get one and always fall back to the
  // checkpoint-and-replay path above, matching P3's "适用范围".
  const UNDO_PATCH_BUDGET_BYTES = 64 * 1024 * 1024;
  const undoPatches = new MaskUndoPatches<CommandPatch>(UNDO_PATCH_BUDGET_BYTES);

  function captureAlphaSnapshot(): Uint8Array {
    // The mirror already holds the binarized alpha of every pixel, so a
    // checkpoint is a plain copy instead of a 76 ms full-frame read at 4096².
    // Restoring from it loses the antialiased fringe of old strokes, but both
    // coverage and export threshold at 128, so the result is identical.
    return mirror.slice();
  }

  function restoreAlphaSnapshot(alpha: Uint8Array) {
    mirror.set(alpha);
    const imageData = context.createImageData(width, height);
    const data = imageData.data;
    for (let index = 0, pixel = 0; index < alpha.length; index += 1, pixel += 4) {
      if (!alpha[index]) continue;
      data[pixel] = MARK_RGB.r;
      data[pixel + 1] = MARK_RGB.g;
      data[pixel + 2] = MARK_RGB.b;
      data[pixel + 3] = 255;
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

  /**
   * Trace a stroke into the current path, from `fromIndex` (0 = a single dab)
   * up to but not including `toIndex` (default: the rest of the points).
   */
  function traceStroke(
    target: CanvasRenderingContext2D,
    command: StrokeCommand,
    fromIndex: number,
    toIndex: number = command.points.length
  ) {
    const { points } = command;
    if (!points.length) return;
    target.beginPath();
    const startIndex = Math.max(0, fromIndex - 1);
    target.moveTo(points[startIndex].x, points[startIndex].y);
    for (let index = startIndex + 1; index < toIndex; index += 1) {
      target.lineTo(points[index].x, points[index].y);
    }
  }

  function drawStroke(
    target: CanvasRenderingContext2D,
    command: StrokeCommand,
    fromIndex = 1,
    toIndex: number = command.points.length
  ) {
    const { points, size, tool } = command;
    if (!points.length) return;
    strokeContext(target);
    target.lineWidth = Math.max(1, size);
    target.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over';
    traceStroke(target, command, fromIndex, toIndex);
    target.stroke();
    target.restore();
  }

  /**
   * Replay a stroke command as the same sequence of incremental `stroke()`
   * calls live painting made, using its recorded segment boundaries. Falls
   * back to one continuous path for a command with no segments (imported
   * mask replay path never constructs one; nothing else should).
   */
  function applyStrokeCommand(target: CanvasRenderingContext2D, command: StrokeCommand) {
    if (!command.segments.length) {
      drawStroke(target, command);
      return;
    }
    let from = 0;
    for (const to of command.segments) {
      drawStroke(target, command, from, to);
      from = to;
    }
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
        applyStrokeCommand(context, command);
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

  /**
   * Read `region` back once: refresh the mark mirror from the canvas, count its
   * marked pixels, and clear the protection bits of every pixel the user just
   * painted over (I2 — painting again is changing your mind).
   */
  function readMarkedRegion(region: MaskRect): number {
    const data = context.getImageData(region.x, region.y, region.width, region.height).data;
    let marked = 0;
    for (let row = 0; row < region.height; row += 1) {
      const mirrorRow = (region.y + row) * width + region.x;
      const pixelRow = row * region.width * 4;
      for (let column = 0; column < region.width; column += 1) {
        const index = mirrorRow + column;
        if (data[pixelRow + column * 4 + 3] >= MARK_ALPHA_THRESHOLD) {
          mirror[index] = 1;
          marked += 1;
          if (protectedMirror) protectedMirror[index] = 0;
        } else {
          mirror[index] = 0;
        }
      }
    }
    return marked;
  }

  /** Rebuild the whole mark mirror from the canvas and return its marked count. */
  function rebuildMirror(): number {
    const data = context.getImageData(0, 0, width, height).data;
    let marked = 0;
    for (let index = 0, pixel = 3; index < mirror.length; index += 1, pixel += 4) {
      const on = data[pixel] >= MARK_ALPHA_THRESHOLD ? 1 : 0;
      mirror[index] = on;
      marked += on;
    }
    return marked;
  }

  /** Rebuild the mirror and hand its total to the tracker. */
  function recountMarkedPixels() {
    tracker.resetTo(rebuildMirror());
  }

  function ensureProtectedMirror(): Uint8Array {
    if (!protectedMirror) protectedMirror = new Uint8Array(width * height);
    return protectedMirror;
  }

  /**
   * Snapshot one rectangle's exact alpha bytes and protection bits before a
   * command draws over it (undo patch, plan Phase 4 / P4).
   *
   * This is one extra `getImageData` per segment beyond what P1 already
   * reads — cheap at segment size (fractions of a millisecond, per the plan's
   * bench) and nowhere near the whole-bbox read P1 eliminated. Storing the
   * real alpha bytes rather than the binarized mirror is what lets undo
   * restore antialiased stroke edges exactly instead of the ~0.1-0.2pp of
   * drift a checkpoint-and-replay recount used to leave.
   */
  function captureFragment(rect: MaskRect): PixelFragment {
    const data = context.getImageData(rect.x, rect.y, rect.width, rect.height).data;
    const alpha = new Uint8ClampedArray(rect.width * rect.height);
    for (let index = 0, pixel = 3; index < alpha.length; index += 1, pixel += 4) {
      alpha[index] = data[pixel];
    }
    let protect: Uint8Array | null = null;
    if (protectedMirror) {
      protect = new Uint8Array(rect.width * rect.height);
      for (let row = 0; row < rect.height; row += 1) {
        const mirrorRow = (rect.y + row) * width + rect.x;
        const localRow = row * rect.width;
        for (let column = 0; column < rect.width; column += 1) {
          protect[localRow + column] = protectedMirror[mirrorRow + column];
        }
      }
    }
    return { rect, alpha, protect };
  }

  function fragmentBytes(fragment: PixelFragment): number {
    return fragment.alpha.length + (fragment.protect?.length ?? 0);
  }

  /** Write one fragment's pre-command pixels back, and its mirror bits with it. */
  function restoreFragment(fragment: PixelFragment) {
    const { rect, alpha, protect } = fragment;
    const imageData = context.createImageData(rect.width, rect.height);
    const data = imageData.data;
    for (let index = 0, pixel = 3; index < alpha.length; index += 1, pixel += 4) {
      data[pixel - 3] = MARK_RGB.r;
      data[pixel - 2] = MARK_RGB.g;
      data[pixel - 1] = MARK_RGB.b;
      data[pixel] = alpha[index];
    }
    context.putImageData(imageData, rect.x, rect.y);
    for (let row = 0; row < rect.height; row += 1) {
      const mirrorRow = (rect.y + row) * width + rect.x;
      const localRow = row * rect.width;
      for (let column = 0; column < rect.width; column += 1) {
        const index = mirrorRow + column;
        const local = localRow + column;
        mirror[index] = alpha[local] >= MARK_ALPHA_THRESHOLD ? 1 : 0;
        if (protectedMirror) protectedMirror[index] = protect ? protect[local] : 0;
      }
    }
  }

  /**
   * Undo one stroke/shape command from its patch: replay its fragments in
   * reverse (last-drawn first), so a pixel touched by more than one segment
   * ends up with whatever was under the earliest one, fold the stored
   * marked-pixel delta out of the tracker in O(1), and invalidate only the
   * command's own bounding box instead of the whole processed view.
   */
  function applyUndoPatch(patch: CommandPatch) {
    if (!protectedMirror && patch.fragments.some((fragment) => fragment.protect)) {
      ensureProtectedMirror();
    }
    for (let index = patch.fragments.length - 1; index >= 0; index -= 1) {
      restoreFragment(patch.fragments[index]);
    }
    tracker.applyDelta(patch.markedDelta, 0);
    invalidateProcessed(patch.rect);
  }

  /**
   * Redo one stroke/shape command: replay it forward (segment-precise for
   * strokes) and re-derive the mirror/protection/tracker state from a single
   * read over the command's own bounding box — bounded to that command, never
   * the whole canvas, but not O(segment) the way live painting is. Redo is a
   * discrete click rather than a per-frame cost, so that trade is worth
   * keeping the code path simple.
   */
  function applyRedoPatch(command: StrokeCommand | ShapeCommand, patch: CommandPatch) {
    const before = countMirrorRegion(mirror, patch.rect, width);
    if (command.kind === 'stroke') applyStrokeCommand(context, command);
    else fillShape(context, command);
    const after = readMarkedRegion(patch.rect);
    tracker.applyDelta(before, after);
    const erasing = command.kind === 'stroke' ? command.tool === 'eraser' : command.mode === 'erase';
    if (erasing) {
      if (command.kind === 'stroke') protectEraserStroke(command, 0, patch.rect);
      else protectEraseShape(command, patch.rect);
    }
    invalidateProcessed(patch.rect);
  }

  /**
   * Add the pixels an eraser stroke (or an erase-mode shape) covered to the
   * protection set.
   *
   * The geometry is re-traced into a box-sized scratch canvas rather than read
   * off the marks canvas: erasing a pixel that was never marked is still an
   * explicit instruction to keep it untouched, and an alpha read-back cannot
   * tell that pixel apart from one the user never visited.
   */
  function protectGeometry(rect: MaskRect, trace: (target: CanvasRenderingContext2D) => void) {
    const box = clampRect(rect, width, height);
    if (!box) return;
    const canvas = protectCanvas ?? (protectCanvas = createCanvas(1, 1));
    if (canvas.width < box.width || canvas.height < box.height) {
      canvas.width = Math.max(canvas.width, box.width);
      canvas.height = Math.max(canvas.height, box.height);
    }
    const target = context2d(canvas, true);
    target.setTransform(1, 0, 0, 1, 0, 0);
    target.clearRect(0, 0, canvas.width, canvas.height);
    target.save();
    target.translate(-box.x, -box.y);
    target.globalCompositeOperation = 'source-over';
    target.strokeStyle = MARK_COLOR;
    target.fillStyle = MARK_COLOR;
    target.lineCap = 'round';
    target.lineJoin = 'round';
    trace(target);
    target.restore();

    const protect = ensureProtectedMirror();
    const data = target.getImageData(0, 0, box.width, box.height).data;
    for (let row = 0; row < box.height; row += 1) {
      const mirrorRow = (box.y + row) * width + box.x;
      const pixelRow = row * box.width * 4;
      for (let column = 0; column < box.width; column += 1) {
        const index = mirrorRow + column;
        // A pixel the eraser barely grazed is still marked, and a marked pixel
        // must never be protected: the protection set subtracts from the edit
        // area, so it would drop a pixel the user painted (I1).
        if (mirror[index]) continue;
        if (data[pixelRow + column * 4 + 3] >= MARK_ALPHA_THRESHOLD) protect[index] = 1;
      }
    }
  }

  function protectEraserStroke(command: StrokeCommand, fromIndex: number, rect: MaskRect | null) {
    if (!rect) return;
    protectGeometry(rect, (target) => {
      target.lineWidth = Math.max(1, command.size);
      traceStroke(target, command, fromIndex);
      target.stroke();
    });
  }

  function protectEraseShape(command: ShapeCommand, rect: MaskRect | null) {
    if (!rect) return;
    protectGeometry(rect, (target) => {
      fillShape(target, command);
    });
  }

  function releaseProtectedMirror() {
    protectedMirror = null;
  }

  /**
   * Rebuild the protection set from the command history after a replay.
   *
   * The protection set follows the same history as the marks: it starts empty,
   * the user's erasers add to it, and painting over one takes it back out. A
   * replay therefore re-rasterizes every eraser's geometry and skips whatever
   * is marked in the final state — which is what keeps P ∩ M empty. Were a
   * marked pixel left in P, the smoothed union would subtract it again and the
   * edit area would stop containing something the user painted (I1).
   *
   * Only erasers matter here, so a history without any costs nothing. An
   * invert's protection ("keep what I painted") is not reconstructed — undo
   * patches (plan Phase 4) carry the protection set per command and make this
   * exact; until then the set can only come out too small, never too large.
   */
  function rebuildProtection() {
    releaseProtectedMirror();
    for (const command of commands) {
      if (command.kind === 'clear') {
        releaseProtectedMirror();
        continue;
      }
      if (command.kind === 'stroke') {
        if (command.tool !== 'eraser') continue;
        protectEraserStroke(command, 0, strokeRectFor(command.points, command.size, width, height));
        continue;
      }
      if (command.kind === 'rect' || command.kind === 'ellipse' || command.kind === 'lasso') {
        if (command.mode !== 'erase') continue;
        protectEraseShape(command, shapeRectFor(command.points, width, height));
      }
    }
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
      processedDirty.markFull();
      return;
    }
    processedDirty.markRect(rect);
  }

  function ensureProcessedCanvas(): HTMLCanvasElement {
    if (!processedCanvas) processedCanvas = createCanvas(width, height);
    return processedCanvas;
  }

  function ensureBlurCanvas(): HTMLCanvasElement {
    if (!blurCanvas) blurCanvas = createCanvas(width, height);
    return blurCanvas;
  }

  function ensureProcessedMirror(): Uint8Array {
    if (!processedMirror) processedMirror = new Uint8Array(width * height);
    return processedMirror;
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
    processedMirror = null;
    processedMarked = 0;
    processedDirty.clear();
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
    processedMarked = binarizeUnion(imageData.data, { x: 0, y: 0, width, height }, {
      width,
      mirror,
      protect: protectedMirror,
      target: ensureProcessedMirror()
    });
    blur.putImageData(imageData, 0, 0);

    target.save();
    target.globalCompositeOperation = 'copy';
    target.clearRect(0, 0, width, height);
    target.drawImage(blurCanvas as HTMLCanvasElement, 0, 0);
    target.restore();
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
    const before = processedMirror ? countMirrorRegion(processedMirror, output, width) : 0;

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
    const after = binarizeUnion(imageData.data, output, {
      width,
      mirror,
      protect: protectedMirror,
      target: ensureProcessedMirror()
    });
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
    const full: MaskRect = { x: 0, y: 0, width, height };
    const padding = Math.ceil(smoothPx * 3) + 2;

    if (processedDirty.isFull) {
      repaintProcessedFull(target, blur);
    } else {
      const regions = processedDirty.list
        .map((rect) => expandRect(rect, padding, width, height))
        .filter((rect): rect is MaskRect => rect !== null);
      const totalArea = regions.reduce((sum, rect) => sum + rectArea(rect), 0);
      const coversAll = regions.some((rect) => rectCoversAll(rect, width, height));
      if (coversAll || totalArea > rectArea(full) * PROCESSED_REGION_MAX_RATIO) {
        repaintProcessedFull(target, blur);
      } else {
        // Each region is repainted from the untouched `marks` canvas, so
        // processing an overlap twice just recomputes the same already-correct
        // pixels (delta 0 the second time) rather than double-counting them.
        for (const region of regions) repaintProcessedRegion(target, blur, region);
      }
    }
    processedDirty.clear();
    processedStale = false;
    // The processed view is Stage A of the gap-fill pipeline: its binary alpha
    // is what the worker receives, so every repaint invalidates the additions
    // layer computed from it.
    regionVersion += 1;
  }

  /**
   * Edited-area ratio of the base region (the processed view when smoothing is
   * on, the raw marks otherwise), without the automatic additions. The count is
   * maintained by the repaint above, so this never reads pixels back.
   *
   * While the smoothed view is stale this reports the marks themselves: the
   * repaint is debounced, and the readout must never sit on a number the user
   * has already changed, nor on the previous stroke's blurred contour.
   */
  function processedEditableRatio(): number {
    if (smoothPx <= 0 || !processedCanvas || processedStale) return tracker.coverage();
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
    layerContext.imageSmoothingEnabled = false;
    layerContext.drawImage(decoded, 0, 0, width, height);

    const imageData = layerContext.getImageData(0, 0, width, height);
    const data = imageData.data;
    const sourceBytes = new Uint8Array(await source.arrayBuffer());
    const hasTransparency = data.some((value, index) => index % 4 === 3 && value < 255);
    const alphaMask = (pngHasAlphaChannel(sourceBytes) || isWebp(sourceBytes)) && hasTransparency;
    for (let index = 0; index < data.length; index += 4) {
      const editable = alphaMask
        ? data[index + 3] < 128
        : (data[index] * 299 + data[index + 1] * 587 + data[index + 2] * 114) / 1000 >= 128;
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
    importLayerCache = { source, layer: cached, alphaMask };
    return cached;
  }

  async function preparedImportLayer(source: Blob): Promise<CanvasImageSource> {
    if (importLayerCache && importLayerCache.source === source) return importLayerCache.layer;
    return buildImportLayer(source, await decodeImage(source));
  }

  function coverage(): number {
    return tracker.coverage();
  }

  /**
   * Everything that ends up editable: the base region (marks, or the smoothed
   * union of them) plus the automatic additions. The preview, the coverage
   * readout and the export all read this one number.
   */
  function effectiveCoverage(): number {
    const total = width * height;
    if (!total) return 0;
    return (Math.round(processedEditableRatio() * total) + autoCount) / total;
  }

  /**
   * Rebuild the processed view at `next` radius. A different radius changes
   * every pixel's blur input, so the whole frame has to be repainted regardless
   * of what was dirty.
   */
  function applySmoothing(next: number) {
    if (next !== smoothPx) processedDirty.markFull();
    smoothPx = next;
    repaintProcessed();
    return processedEditableRatio();
  }

  function releaseAutoLayer() {
    releaseImageSource(autoSource);
    autoSource = null;
    autoBbox = null;
    autoCount = 0;
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
        undoPatches.dropAbove(commands.length);
        redoStack = [];
        activeShape = { kind: tool, mode, points: [point] };
        return;
      }
      const command: StrokeCommand = { kind: 'stroke', tool, size, points: [point], segments: [] };
      undoPatches.dropAbove(commands.length);
      commands.push(command);
      redoStack = [];
      activeStroke = command;
      patchFragments = [];
      patchRect = null;
      patchStartMarked = tracker.markedCount();
      const rect = strokeRectFor(command.points, size, width, height);
      if (rect) {
        // The mirror already knows which pixels were marked before this
        // segment, so the "before" half of the delta costs no GPU read.
        const before = countMirrorRegion(mirror, rect, width);
        patchFragments.push(captureFragment(rect));
        patchRect = rect;
        drawStroke(context, command, 0);
        command.segments.push(command.points.length);
        const after = readMarkedRegion(rect);
        tracker.applyDelta(before, after);
        if (tool === 'eraser') protectEraserStroke(command, 0, rect);
        invalidateProcessed(rect);
      }
      regionVersion += 1;
    },

    extendStroke(points: MaskPoint[], constrainShape = false) {
      if (!points.length) return;
      if (activeShape) {
        // A shape only touches the marks canvas on release, so there is no
        // processed repaint to schedule while dragging.
        if (activeShape.kind === 'rect' || activeShape.kind === 'ellipse') {
          const start = activeShape.points[0];
          const end = points[points.length - 1];
          if (constrainShape) {
            const length = Math.max(Math.abs(end.x - start.x), Math.abs(end.y - start.y));
            activeShape.points = [start, {
              x: start.x + (end.x < start.x ? -length : length),
              y: start.y + (end.y < start.y ? -length : length)
            }];
          } else {
            activeShape.points = [start, end];
          }
        } else {
          activeShape.points.push(...points);
        }
        return;
      }
      if (!activeStroke) return;
      const fromIndex = activeStroke.points.length;
      activeStroke.points.push(...points);
      // The segment box has to include the point the segment is drawn *from*:
      // `drawStroke` starts its path at `points[fromIndex - 1]`, and a pointer
      // jump longer than the brush gap would otherwise leave the midpoint out
      // of the count and the dirty region (P2).
      const segment = [activeStroke.points[fromIndex - 1], ...points];
      const rect = strokeRectFor(segment, activeStroke.size, width, height);
      if (rect) {
        const before = countMirrorRegion(mirror, rect, width);
        // The undo patch for this stroke is built from these same "before
        // draw" fragments (P4) — one extra small read beyond what P1 already
        // did for `before`, still nowhere near the whole-bbox reads it fixed.
        patchFragments.push(captureFragment(rect));
        patchRect = patchRect ? unionRect(patchRect, rect) : rect;
        drawStroke(context, activeStroke, fromIndex);
        activeStroke.segments.push(activeStroke.points.length);
        const after = readMarkedRegion(rect);
        tracker.applyDelta(before, after);
        if (activeStroke.tool === 'eraser') protectEraserStroke(activeStroke, fromIndex, rect);
        invalidateProcessed(rect);
      }
      regionVersion += 1;
    },

    endStroke() {
      if (activeShape) {
        const command = activeShape;
        activeShape = null;
        const rect = shapeRectFor(command.points, width, height);
        const before = rect ? countMirrorRegion(mirror, rect, width) : 0;
        const fragment = rect ? captureFragment(rect) : null;
        const markedBeforeCommand = tracker.markedCount();
        if (fillShape(context, command)) {
          undoPatches.dropAbove(commands.length);
          commands.push(command);
          redoStack = [];
          if (rect) {
            tracker.applyDelta(before, readMarkedRegion(rect));
            if (command.mode === 'erase') protectEraseShape(command, rect);
          }
          invalidateProcessed(rect);
          maybeCheckpoint();
          if (rect && fragment) {
            const patch: CommandPatch = {
              fragments: [fragment],
              rect,
              markedDelta: tracker.markedCount() - markedBeforeCommand
            };
            undoPatches.record(commands.length, fragmentBytes(fragment), patch);
          }
        }
      }
      if (activeStroke) {
        activeStroke = null;
        if (patchFragments.length && patchRect) {
          const patch: CommandPatch = {
            fragments: patchFragments,
            rect: patchRect,
            markedDelta: tracker.markedCount() - patchStartMarked
          };
          const bytes = patchFragments.reduce((sum, fragment) => sum + fragmentBytes(fragment), 0);
          undoPatches.record(commands.length, bytes, patch);
        }
        patchFragments = [];
        patchRect = null;
        maybeCheckpoint();
      }
      regionVersion += 1;
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
      // A stroke/shape command's afterIndex is exactly the command-list
      // length it left behind — the same value beginStroke/endStroke recorded
      // its patch under — so this has to be read before the pop below.
      const targetIndex = commands.length;
      const command = commands.pop();
      if (!command) return;
      redoStack.push(command);
      const patch = isPatchable(command) ? undoPatches.get(targetIndex) : null;
      if (patch) {
        applyUndoPatch(patch);
      } else {
        await replayFrom(commands.length);
        recountMarkedPixels();
        rebuildProtection();
        invalidateProcessed();
      }
      regionVersion += 1;
    },

    async redo(): Promise<void> {
      const command = redoStack.pop();
      if (!command) return;
      commands.push(command);
      const patch = isPatchable(command) ? undoPatches.get(commands.length) : null;
      if (patch && isPatchable(command)) {
        applyRedoPatch(command, patch);
      } else {
        await applyCommandAsync(command);
        recountMarkedPixels();
        rebuildProtection();
        invalidateProcessed();
      }
      maybeCheckpoint();
      regionVersion += 1;
    },

    clear() {
      undoPatches.dropAbove(commands.length);
      commands.push({ kind: 'clear' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      tracker.clear();
      mirror.fill(0);
      releaseProtectedMirror();
      context.clearRect(0, 0, width, height);
      invalidateProcessed();
      maybeCheckpoint();
      regionVersion += 1;
    },

    invert() {
      undoPatches.dropAbove(commands.length);
      commands.push({ kind: 'invert' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      // Inverting means "keep what I painted": the pre-invert mark set becomes
      // the protection set, so gap filling cannot undo the selection.
      const previous = tracker.markedCount();
      if (previous > 0) {
        protectedMirror = mirror.slice();
      } else {
        releaseProtectedMirror();
      }
      tracker.invert();
      invertMarks();
      for (let index = 0; index < mirror.length; index += 1) {
        mirror[index] = mirror[index] ? 0 : 1;
      }
      invalidateProcessed();
      maybeCheckpoint();
      regionVersion += 1;
    },

    async importFromPng(file: Blob, mode: MaskImportMode = 'replace', allowScale = false) {
      if (file.size > MAX_MASK_FILE_BYTES) throw new MaskImportError('too-large');
      const bytes = new Uint8Array(await file.arrayBuffer());
      const isPng = Boolean(readPngSize(bytes));
      if (!isPng && !isJpeg(bytes) && !isWebp(bytes)) throw new MaskImportError('not-png');

      const decoded = await decodeImage(file);
      // Read the size before closing the bitmap: a closed ImageBitmap reports 0x0.
      const decodedWidth = decoded.width;
      const decodedHeight = decoded.height;
      const scaled = decodedWidth !== width || decodedHeight !== height;
      if (scaled && (!allowScale || Math.abs(decodedWidth / decodedHeight / (width / height) - 1) > 0.01)) {
        if (typeof ImageBitmap !== 'undefined' && decoded instanceof ImageBitmap) decoded.close();
        throw new MaskImportError('size', { width: decodedWidth, height: decodedHeight });
      }

      const command: ImportCommand = { kind: 'import', source: file, replace: mode === 'replace' };
      undoPatches.dropAbove(commands.length);
      commands.push(command);
      redoStack = [];
      drawImportLayer(await buildImportLayer(file, decoded), command.replace);
      if (typeof ImageBitmap !== 'undefined' && decoded instanceof ImageBitmap) decoded.close();
      recountMarkedPixels();
      // An imported mask is a fresh starting point: nothing in it was "erased".
      releaseProtectedMirror();
      invalidateProcessed();
      maybeCheckpoint();
      regionVersion += 1;
      return { coverage: tracker.coverage(), blackWhite: !importLayerCache?.alphaMask, scaled };
    },

    /**
     * Raw marks canvas as a lossless PNG. This is an internal re-editing
     * snapshot (plan U2), not the upstream export: it keeps every antialiased
     * edge exactly, where `exportPng` binarizes to the alpha==0/255 contract.
     */
    async exportMarksSnapshot(): Promise<Blob> {
      return canvasBlob(marks);
    },

    /** Protection set P as a PNG (alpha 255 = protected), or null when nothing is. */
    async exportProtectedSnapshot(): Promise<Blob | null> {
      if (!protectedMirror) return null;
      const scratch = createCanvas(width, height);
      const scratchContext = context2d(scratch, true);
      const imageData = scratchContext.createImageData(width, height);
      const data = imageData.data;
      for (let index = 0, pixel = 3; index < protectedMirror.length; index += 1, pixel += 4) {
        if (protectedMirror[index]) data[pixel] = 255;
      }
      scratchContext.putImageData(imageData, 0, 0);
      const blob = await canvasBlob(scratch);
      scratch.width = 0;
      scratch.height = 0;
      return blob;
    },

    /**
     * Replace the document with a previously exported marks/protection
     * snapshot (plan U2's non-destructive re-edit): reopening a mask this way
     * restores the original strokes and protection set instead of importing
     * the already-processed export as a fresh, undo-less starting point. The
     * command history itself is not carried over — there is nothing to
     * replay it from — so this is a new baseline the same way an import is.
     */
    async restoreSnapshot(marksBlob: Blob, protectedBlob: Blob | null, smoothing: number): Promise<number> {
      const decodedMarks = await decodeImage(marksBlob);
      context.save();
      context.globalCompositeOperation = 'copy';
      context.clearRect(0, 0, width, height);
      context.drawImage(decodedMarks, 0, 0);
      context.restore();
      if (typeof ImageBitmap !== 'undefined' && decodedMarks instanceof ImageBitmap) decodedMarks.close();
      recountMarkedPixels();

      if (protectedBlob) {
        const decodedProtect = await decodeImage(protectedBlob);
        const scratch = createCanvas(width, height);
        const scratchContext = context2d(scratch, true);
        scratchContext.drawImage(decodedProtect, 0, 0);
        const data = scratchContext.getImageData(0, 0, width, height).data;
        const protect = ensureProtectedMirror();
        for (let index = 0, pixel = 3; index < protect.length; index += 1, pixel += 4) {
          protect[index] = data[pixel] >= MARK_ALPHA_THRESHOLD ? 1 : 0;
        }
        scratch.width = 0;
        scratch.height = 0;
        if (typeof ImageBitmap !== 'undefined' && decodedProtect instanceof ImageBitmap) decodedProtect.close();
      } else {
        releaseProtectedMirror();
      }

      commands = [];
      redoStack = [];
      checkpointer.clear();
      undoPatches.clear();
      activeStroke = null;
      activeShape = null;
      return applySmoothing(normalizeSmoothPx(smoothing, width));
    },

    /** Change the edge-smoothing radius and return the base editable ratio. */
    setSmoothing(px: number) {
      const next = normalizeSmoothPx(px, width);
      if (next === smoothPx && !processedStale) return processedEditableRatio();
      return applySmoothing(next);
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

    /** Base editable ratio: the processed view when smoothing is on. */
    baseCoverage() {
      return processedEditableRatio();
    },

    /** Ratio of the automatic additions layer, 0 when it is empty. */
    autoCoverage() {
      const total = width * height;
      return total ? autoCount / total : 0;
    },

    /** What the preview, the readout and the export all agree on. */
    effectiveCoverage,

    hasActiveShape() {
      return activeShape !== null;
    },

    /** Draw the in-progress rect/ellipse/lasso into a context in image coordinates. */
    drawActiveShapePreview(target: CanvasRenderingContext2D) {
      if (!activeShape) return;
      fillShape(target, activeShape, activeShape.mode === 'erase' ? '#000' : MARK_PREVIEW_COLOR);
    },

    /** Change the gap-fill parameters; null turns the whole pass off. */
    setRegionParams(params: MaskRegionParams | null) {
      regionParams = params;
      releaseAutoLayer();
      regionVersion += 1;
    },

    get regionVersion() {
      return regionVersion;
    },

    /** True while a newer set of inputs is waiting for a worker result. */
    regionPending() {
      return regionParams !== null && appliedRegionVersion !== regionVersion;
    },

    /**
     * Inputs for one gap-fill pass. The mirrors are copied because the worker
     * owns whatever it receives; at 4096² that copy is ~12 ms, the same order
     * as the structured clone the hand-off costs.
     */
    regionSnapshot(edge: EdgeMap | null = null): MaskRegionRequest | null {
      if (!regionParams) return null;
      return {
        version: regionVersion,
        width,
        height,
        a: (smoothPx > 0 && processedMirror ? processedMirror : mirror).slice(),
        p: protectedMirror ? protectedMirror.slice() : null,
        params: regionParams,
        edge: regionParams.edgeSnap ? edge : null
      };
    },

    /**
     * Accept a gap-fill result. Stale results (a newer change landed while the
     * worker was busy) are dropped, and their layer is released here so the
     * caller never has to think about the bitmap's lifetime.
     */
    applyAutoLayer(result: {
      version: number;
      count: number;
      bbox: MaskRect | null;
      source: CanvasImageSource | null;
    }): boolean {
      if (result.version !== regionVersion) {
        releaseImageSource(result.source);
        return false;
      }
      const previous = autoSource;
      autoSource = result.count > 0 ? result.source : null;
      autoBbox = result.count > 0 && result.bbox ? result.bbox : null;
      autoCount = result.count;
      appliedRegionVersion = result.version;
      releaseImageSource(previous);
      return true;
    },

    /** The additions layer D, when one is available. */
    get autoLayer(): { source: CanvasImageSource; bbox: MaskRect } | null {
      return autoSource && autoBbox ? { source: autoSource, bbox: autoBbox } : null;
    },

    get autoPixelCount() {
      return autoCount;
    },

    async exportPng(options: { smoothing?: number } = {}) {
      const smoothing = normalizeSmoothPx(options.smoothing ?? smoothPx, width);
      if (smoothing !== smoothPx) applySmoothing(smoothing);
      else if (processedStale) repaintProcessed();

      const canvas = createCanvas(width, height);
      const exportContext = context2d(canvas, true);
      exportContext.fillStyle = '#000';
      exportContext.fillRect(0, 0, width, height);
      exportContext.globalCompositeOperation = 'destination-out';

      // The upstream contract only edits alpha == 0 pixels, so a soft gradient
      // cannot survive the round trip. Drawing the processed view directly
      // (which is already binarized, and already the union of the blurred
      // contour with the marks) replaces the second full-frame blur and the two
      // scratch canvases this used to allocate.
      exportContext.drawImage(smoothPx > 0 && processedCanvas ? processedCanvas : marks, 0, 0);
      const layer = autoSource && autoBbox ? { source: autoSource, bbox: autoBbox } : null;
      if (layer) {
        exportContext.drawImage(
          layer.source,
          layer.bbox.x,
          layer.bbox.y,
          layer.bbox.width,
          layer.bbox.height
        );
      }
      exportContext.globalCompositeOperation = 'source-over';

      const imageData = exportContext.getImageData(0, 0, width, height);
      const ratio = binarizeMaskAlphaData(imageData.data);
      exportContext.putImageData(imageData, 0, 0);

      if (import.meta.env.DEV) {
        // Preview, readout and export are one promise to the user; the dev
        // build is where a drifting counter or a half-applied layer shows up.
        const expected = effectiveCoverage();
        if (Math.abs(ratio - expected) > 1e-6) {
          throw new Error(
            `mask export coverage ${ratio} disagrees with the editor readout ${expected}`
          );
        }
      }

      return { blob: await canvasBlob(canvas), coverage: ratio };
    },

    dispose() {
      commands = [];
      redoStack = [];
      checkpointer.clear();
      undoPatches.clear();
      activeStroke = null;
      activeShape = null;
      patchFragments = [];
      patchRect = null;
      tracker.resetTo(0);
      mirror.fill(0);
      releaseProtectedMirror();
      releaseAutoLayer();
      releaseImportLayerCache();
      releaseProcessedLayers();
      marks.width = 0;
      marks.height = 0;
    }
  };
}
