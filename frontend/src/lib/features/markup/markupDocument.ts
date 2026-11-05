// Annotation document for the web markup editor.
//
// The document owns one native-resolution RGBA layer (transparent at start) and
// a replayable command log. Commands are recorded in *image pixels*, so later
// zoom, pan, DPR and window changes can never move a stroke. The primary image
// is never drawn into this layer: the exported PNG contains only annotations.
//
// Preview and export share `renderMarkupCommand`, so what the user sees is what
// the upstream model receives.

import type { MarkupPoint, MarkupSize } from './markupViewport';

export const MAX_MARKUP_PIXELS = 16_777_216;
export const MAX_MARKUP_EDGE = 32_767;
/** Undo depth. Older commands are folded into the base checkpoint, never dropped. */
export const MAX_HISTORY = 100;
export const MAX_TEXT_LENGTH = 80;

export type MarkupTool = 'pen' | 'arrow' | 'rect' | 'ellipse' | 'text' | 'eraser' | 'pan';

export const MARKUP_COLORS = [
  { id: 'red', value: '#e5322d' },
  { id: 'yellow', value: '#f5c518' },
  { id: 'blue', value: '#2563eb' },
  { id: 'green', value: '#16a34a' },
  { id: 'black', value: '#111111' },
  { id: 'white', value: '#ffffff' }
] as const;
export type MarkupColorId = (typeof MARKUP_COLORS)[number]['id'];
export const DEFAULT_MARKUP_COLOR: string = MARKUP_COLORS[0].value;

type CommandBase = { readonly id: number };
export type PenCommand = CommandBase & {
  readonly kind: 'pen';
  readonly color: string;
  readonly width: number;
  /** Flat [x0, y0, x1, y1, ...] in image pixels. */
  readonly points: readonly number[];
};
export type EraseCommand = CommandBase & {
  readonly kind: 'erase';
  readonly width: number;
  readonly points: readonly number[];
};
export type ArrowCommand = CommandBase & {
  readonly kind: 'arrow';
  readonly color: string;
  readonly width: number;
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
};
export type RectCommand = CommandBase & {
  readonly kind: 'rect';
  readonly color: string;
  readonly width: number;
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
};
export type EllipseCommand = CommandBase & {
  readonly kind: 'ellipse';
  readonly color: string;
  readonly width: number;
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
};
export type TextCommand = CommandBase & {
  readonly kind: 'text';
  readonly color: string;
  readonly size: number;
  /** Top-left of the text box in image pixels. */
  readonly x: number;
  readonly y: number;
  readonly text: string;
};
export type ClearCommand = CommandBase & { readonly kind: 'clear' };
export type MarkupCommand =
  | PenCommand
  | EraseCommand
  | ArrowCommand
  | RectCommand
  | EllipseCommand
  | TextCommand
  | ClearCommand;

type WithoutId<T> = T extends unknown ? Omit<T, 'id'> : never;
export type MarkupCommandInput = WithoutId<MarkupCommand>;

/**
 * Browser-only editing state kept next to an applied PNG so the same page
 * session can resume drawing. It never leaves the browser; the PNG is the only
 * thing that is uploaded. Treat every field as immutable.
 */
export type MarkupEditorState = {
  readonly width: number;
  readonly height: number;
  /** PNG of annotations folded out of the undo window or imported, if any. */
  readonly base: Blob | null;
  readonly commands: readonly MarkupCommand[];
};

export type MarkupExport = { blob: Blob; width: number; height: number; state: MarkupEditorState };

export type MarkupErrorCode =
  | 'size'
  | 'too_large'
  | 'empty'
  | 'opaque'
  | 'canvas'
  | 'encode'
  | 'decode'
  | 'busy'
  | 'stale'
  | 'disposed';

export class MarkupDocumentError extends Error {
  readonly code: MarkupErrorCode;
  constructor(code: MarkupErrorCode, message?: string) {
    super(message ?? code);
    this.name = 'MarkupDocumentError';
    this.code = code;
  }
}

export type MarkupContext = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
export type MarkupCanvasLike = {
  width: number;
  height: number;
  getContext(contextId: '2d', options?: CanvasRenderingContext2DSettings): MarkupContext | null;
  toBlob(callback: (blob: Blob | null) => void, type?: string, quality?: number): void;
};
export type MarkupCanvasFactory = (width: number, height: number) => MarkupCanvasLike;

export const createBrowserCanvas: MarkupCanvasFactory = (width, height) => {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas as unknown as MarkupCanvasLike;
};

const COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
export const MARKUP_FONT_FAMILY =
  'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';

export function markupFont(size: number): string {
  return `600 ${size}px ${MARKUP_FONT_FAMILY}`;
}

export function isValidMarkupColor(value: unknown): value is string {
  return typeof value === 'string' && COLOR_PATTERN.test(value);
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function sanitizeMarkupText(text: string): string {
  return text.replace(/[\r\n\t]+/g, ' ').trim().slice(0, MAX_TEXT_LENGTH);
}

/** Default and allowed sizes scale with the image so a 512px and a 4096px image both feel right. */
export function markupSizing(width: number, height: number) {
  const longEdge = Math.max(1, width, height);
  return {
    strokeDefault: clampNumber(Math.round(longEdge / 250), 3, 32),
    strokeMin: 1,
    strokeMax: clampNumber(Math.round(longEdge / 12), 8, 160),
    textDefault: clampNumber(Math.round(longEdge / 28), 16, 200),
    textMin: 12,
    textMax: clampNumber(Math.round(longEdge / 6), 48, 400)
  };
}

export function validateMarkupSize(width: number, height: number, maxPixels = MAX_MARKUP_PIXELS) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
    throw new MarkupDocumentError('size');
  }
  if (width > MAX_MARKUP_EDGE || height > MAX_MARKUP_EDGE || width * height > Math.min(MAX_MARKUP_PIXELS, maxPixels)) {
    throw new MarkupDocumentError('too_large');
  }
}

function sanitizePoints(values: readonly number[], size: MarkupSize): number[] | null {
  if (!Array.isArray(values) || values.length < 2 || values.length % 2 !== 0) return null;
  const points: number[] = new Array(values.length);
  for (let i = 0; i < values.length; i += 2) {
    const x = values[i];
    const y = values[i + 1];
    if (!finite(x) || !finite(y)) return null;
    points[i] = clampNumber(x, 0, size.width);
    points[i + 1] = clampNumber(y, 0, size.height);
  }
  return points;
}

function degenerate(command: { x1: number; y1: number; x2: number; y2: number }) {
  return Math.hypot(command.x2 - command.x1, command.y2 - command.y1) < 1;
}

/**
 * Validates and normalises a command (clamped coordinates, allowed colours and
 * sizes). Returns null for input that must not enter the history: zero-size
 * shapes, empty text, malformed geometry.
 */
export function sanitizeMarkupCommand(input: MarkupCommandInput, size: MarkupSize): MarkupCommandInput | null {
  switch (input.kind) {
    case 'clear':
      return { kind: 'clear' };
    case 'pen':
    case 'erase': {
      if (!finite(input.width) || (input.kind === 'pen' && !isValidMarkupColor(input.color))) return null;
      const points = sanitizePoints(input.points, size);
      if (!points) return null;
      const width = clampNumber(input.width, 1, 512);
      return input.kind === 'pen'
        ? { kind: 'pen', color: input.color.toLowerCase(), width, points }
        : { kind: 'erase', width, points };
    }
    case 'arrow':
    case 'rect':
    case 'ellipse': {
      if (!isValidMarkupColor(input.color) || !finite(input.width)) return null;
      if (![input.x1, input.y1, input.x2, input.y2].every(finite)) return null;
      const next = {
        kind: input.kind,
        color: input.color.toLowerCase(),
        width: clampNumber(input.width, 1, 512),
        x1: clampNumber(input.x1, 0, size.width),
        y1: clampNumber(input.y1, 0, size.height),
        x2: clampNumber(input.x2, 0, size.width),
        y2: clampNumber(input.y2, 0, size.height)
      };
      return degenerate(next) ? null : next;
    }
    case 'text': {
      const text = typeof input.text === 'string' ? sanitizeMarkupText(input.text) : '';
      if (!text || !isValidMarkupColor(input.color) || !finite(input.size) || !finite(input.x) || !finite(input.y)) {
        return null;
      }
      return {
        kind: 'text',
        color: input.color.toLowerCase(),
        size: clampNumber(input.size, 8, 1024),
        x: clampNumber(input.x, 0, size.width),
        y: clampNumber(input.y, 0, size.height),
        text
      };
    }
    default:
      return null;
  }
}

function lineStyle(ctx: MarkupContext, color: string, width: number, erase: boolean) {
  ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
  ctx.strokeStyle = erase ? '#000000' : color;
  ctx.fillStyle = erase ? '#000000' : color;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
}

/**
 * Draws stroke segments for points[fromPoint..]. A lone point becomes a dot.
 * Used both for incremental drawing while the pointer moves and for replay, so
 * live results and undo/redo replays are identical.
 */
export function drawStrokeSegments(
  ctx: MarkupContext,
  points: readonly number[],
  fromPoint: number,
  color: string,
  width: number,
  erase: boolean
) {
  const count = points.length / 2;
  if (count < 1) return;
  ctx.save();
  lineStyle(ctx, color, width, erase);
  if (count === 1) {
    ctx.beginPath();
    ctx.arc(points[0], points[1], width / 2, 0, Math.PI * 2);
    ctx.fill();
  } else {
    for (let i = Math.max(1, fromPoint); i < count; i += 1) {
      ctx.beginPath();
      ctx.moveTo(points[(i - 1) * 2], points[(i - 1) * 2 + 1]);
      ctx.lineTo(points[i * 2], points[i * 2 + 1]);
      ctx.stroke();
    }
  }
  ctx.restore();
}

function drawArrow(ctx: MarkupContext, c: { color: string; width: number; x1: number; y1: number; x2: number; y2: number }) {
  const dx = c.x2 - c.x1;
  const dy = c.y2 - c.y1;
  const length = Math.hypot(dx, dy);
  if (length < 1) return;
  const ux = dx / length;
  const uy = dy / length;
  const headLength = Math.min(length * 0.7, Math.max(c.width * 4.5, 14));
  const headHalfWidth = Math.max(headLength * 0.5, c.width * 1.6);
  const baseX = c.x2 - ux * headLength;
  const baseY = c.y2 - uy * headLength;
  ctx.save();
  lineStyle(ctx, c.color, c.width, false);
  ctx.beginPath();
  ctx.moveTo(c.x1, c.y1);
  // The shaft ends inside the head so the tip stays sharp at any line width.
  ctx.lineTo(baseX + ux * headLength * 0.25, baseY + uy * headLength * 0.25);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(c.x2, c.y2);
  ctx.lineTo(baseX - uy * headHalfWidth, baseY + ux * headHalfWidth);
  ctx.lineTo(baseX + uy * headHalfWidth, baseY - ux * headHalfWidth);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** Renders one command onto `ctx`. `ctx` may carry a view transform (previews). */
export function renderMarkupCommand(ctx: MarkupContext, command: MarkupCommandInput, size: MarkupSize) {
  switch (command.kind) {
    case 'clear':
      ctx.save();
      ctx.globalCompositeOperation = 'destination-out';
      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, size.width, size.height);
      ctx.restore();
      return;
    case 'pen':
      drawStrokeSegments(ctx, command.points, 0, command.color, command.width, false);
      return;
    case 'erase':
      drawStrokeSegments(ctx, command.points, 0, '#000000', command.width, true);
      return;
    case 'arrow':
      drawArrow(ctx, command);
      return;
    case 'rect': {
      ctx.save();
      lineStyle(ctx, command.color, command.width, false);
      ctx.strokeRect(
        Math.min(command.x1, command.x2),
        Math.min(command.y1, command.y2),
        Math.abs(command.x2 - command.x1),
        Math.abs(command.y2 - command.y1)
      );
      ctx.restore();
      return;
    }
    case 'ellipse': {
      ctx.save();
      lineStyle(ctx, command.color, command.width, false);
      ctx.beginPath();
      ctx.ellipse(
        (command.x1 + command.x2) / 2,
        (command.y1 + command.y2) / 2,
        Math.max(0.5, Math.abs(command.x2 - command.x1) / 2),
        Math.max(0.5, Math.abs(command.y2 - command.y1) / 2),
        0,
        0,
        Math.PI * 2
      );
      ctx.stroke();
      ctx.restore();
      return;
    }
    case 'text': {
      ctx.save();
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = command.color;
      ctx.font = markupFont(command.size);
      ctx.textBaseline = 'top';
      ctx.textAlign = 'left';
      ctx.fillText(command.text, command.x, command.y);
      ctx.restore();
      return;
    }
  }
}

export function canvasToBlob(canvas: MarkupCanvasLike): Promise<Blob> {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new MarkupDocumentError('encode'))), 'image/png');
    } catch {
      reject(new MarkupDocumentError('encode'));
    }
  });
}

export type MarkupLayerStats = { nonEmpty: boolean; hasTransparency: boolean };

type ActiveStroke = { kind: 'pen' | 'erase'; color: string; width: number; points: number[] };

export class MarkupDocument {
  readonly width: number;
  readonly height: number;

  private readonly createCanvas: MarkupCanvasFactory;
  private work: MarkupCanvasLike | null;
  private workCtx: MarkupContext | null;
  private base: MarkupCanvasLike | null = null;
  private baseBlob: Blob | null = null;
  private baseBlobStale = false;
  private commands: MarkupCommand[] = [];
  private cursor = 0;
  private folded = 0;
  private nextId = 1;
  private active: ActiveStroke | null = null;
  private revisionCounter = 0;
  private disposed = false;

  private constructor(width: number, height: number, createCanvas: MarkupCanvasFactory) {
    this.width = width;
    this.height = height;
    this.createCanvas = createCanvas;
    this.work = this.allocate();
    this.workCtx = this.contextOf(this.work);
  }

  static create(
    width: number,
    height: number,
    options: { createCanvas?: MarkupCanvasFactory; maxPixels?: number } = {}
  ): MarkupDocument {
    validateMarkupSize(width, height, options.maxPixels);
    return new MarkupDocument(width, height, options.createCanvas ?? createBrowserCanvas);
  }

  /** Rebuilds a working copy from an applied state; the state itself is not mutated. */
  static async fromState(
    state: MarkupEditorState,
    options: { createCanvas?: MarkupCanvasFactory; maxPixels?: number; decode?: (blob: Blob) => Promise<ImageBitmap> } = {}
  ): Promise<MarkupDocument> {
    const doc = MarkupDocument.create(state.width, state.height, options);
    try {
      if (state.base) await doc.importBase(state.base, options.decode);
      const size = { width: state.width, height: state.height };
      for (const command of state.commands) {
        const clean = sanitizeMarkupCommand(command, size);
        if (!clean) continue;
        doc.commands.push(Object.freeze({ ...clean, id: doc.nextId++ }) as MarkupCommand);
      }
      doc.cursor = doc.commands.length;
      doc.rebuild();
      return doc;
    } catch (error) {
      doc.dispose();
      throw error;
    }
  }

  /** Imports an existing PNG (upload / task restore) as an un-undoable base layer. */
  static async fromPng(
    blob: Blob,
    expected: MarkupSize,
    options: { createCanvas?: MarkupCanvasFactory; maxPixels?: number; decode?: (blob: Blob) => Promise<ImageBitmap> } = {}
  ): Promise<MarkupDocument> {
    const doc = MarkupDocument.create(expected.width, expected.height, options);
    try {
      await doc.importBase(blob, options.decode);
      return doc;
    } catch (error) {
      doc.dispose();
      throw error;
    }
  }

  get layer(): MarkupCanvasLike {
    this.assertAlive();
    return this.work as MarkupCanvasLike;
  }

  get size(): MarkupSize {
    return { width: this.width, height: this.height };
  }

  get revision() {
    return this.revisionCounter;
  }

  get canUndo() {
    return !this.disposed && this.cursor > 0 && !this.active;
  }

  get canRedo() {
    return !this.disposed && this.cursor < this.commands.length && !this.active;
  }

  get historyDepth() {
    return this.cursor;
  }

  get hasActiveStroke() {
    return this.active !== null;
  }

  /** True when an imported or folded checkpoint holds marks outside the undo window. */
  get hasBase() {
    return this.base !== null;
  }

  /** Identifies the applied history position; equal keys mean visually identical documents. */
  get stateKey() {
    return `${this.folded}:${this.cursor}:${this.commands[this.cursor - 1]?.id ?? 0}`;
  }

  get isDisposed() {
    return this.disposed;
  }

  private assertAlive() {
    if (this.disposed || !this.work || !this.workCtx) throw new MarkupDocumentError('disposed');
  }

  private allocate(): MarkupCanvasLike {
    const canvas = this.createCanvas(this.width, this.height);
    if (!canvas || canvas.width !== this.width || canvas.height !== this.height) {
      throw new MarkupDocumentError('canvas');
    }
    return canvas;
  }

  private contextOf(canvas: MarkupCanvasLike): MarkupContext {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new MarkupDocumentError('canvas');
    return ctx;
  }

  private ensureBase(): MarkupContext {
    if (!this.base) this.base = this.allocate();
    return this.contextOf(this.base);
  }

  private async importBase(blob: Blob, decode?: (blob: Blob) => Promise<ImageBitmap>) {
    let bitmap: ImageBitmap;
    try {
      bitmap = await (decode ? decode(blob) : createImageBitmap(blob));
    } catch {
      throw new MarkupDocumentError('decode');
    }
    try {
      if (bitmap.width !== this.width || bitmap.height !== this.height) throw new MarkupDocumentError('size');
      if (this.disposed) throw new MarkupDocumentError('disposed');
      this.ensureBase().drawImage(bitmap, 0, 0);
      this.baseBlob = blob;
      this.baseBlobStale = false;
    } finally {
      bitmap.close();
    }
    this.rebuild();
  }

  /** Clears the live layer and replays the base checkpoint plus applied commands. */
  private rebuild() {
    this.assertAlive();
    const ctx = this.workCtx as MarkupContext;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
    ctx.clearRect(0, 0, this.width, this.height);
    if (this.base) ctx.drawImage(this.base as unknown as CanvasImageSource, 0, 0);
    ctx.restore();
    for (let i = 0; i < this.cursor; i += 1) renderMarkupCommand(ctx, this.commands[i], this.size);
    this.revisionCounter += 1;
  }

  private truncateRedo() {
    if (this.cursor < this.commands.length) this.commands.length = this.cursor;
  }

  /** Folds the oldest commands into the base checkpoint so the visible result is kept. */
  private enforceHistoryLimit() {
    while (this.commands.length > MAX_HISTORY) {
      const oldest = this.commands.shift() as MarkupCommand;
      renderMarkupCommand(this.ensureBase(), oldest, this.size);
      this.cursor -= 1;
      this.folded += 1;
      this.baseBlob = null;
      this.baseBlobStale = true;
    }
  }

  private push(clean: MarkupCommandInput): MarkupCommand {
    const command = Object.freeze({ ...clean, id: this.nextId++ }) as MarkupCommand;
    if ('points' in command) Object.freeze(command.points);
    this.truncateRedo();
    this.commands.push(command);
    this.cursor = this.commands.length;
    this.enforceHistoryLimit();
    return command;
  }

  /** Records and draws a finished shape / text / clear as one undoable step. */
  commit(input: MarkupCommandInput): boolean {
    this.assertAlive();
    if (this.active) throw new MarkupDocumentError('busy');
    const clean = sanitizeMarkupCommand(input, this.size);
    if (!clean) return false;
    if (clean.kind === 'clear' && this.cursor === 0 && !this.base) return false;
    const command = this.push(clean);
    renderMarkupCommand(this.workCtx as MarkupContext, command, this.size);
    this.revisionCounter += 1;
    return true;
  }

  clear(): boolean {
    return this.commit({ kind: 'clear' });
  }

  beginStroke(kind: 'pen' | 'erase', point: MarkupPoint, style: { color: string; width: number }): boolean {
    this.assertAlive();
    if (this.active) throw new MarkupDocumentError('busy');
    const probe = sanitizeMarkupCommand(
      kind === 'pen'
        ? { kind: 'pen', color: style.color, width: style.width, points: [point.x, point.y] }
        : { kind: 'erase', width: style.width, points: [point.x, point.y] },
      this.size
    );
    if (!probe || (probe.kind !== 'pen' && probe.kind !== 'erase')) return false;
    this.active = {
      kind,
      color: probe.kind === 'pen' ? probe.color : '#000000',
      width: probe.width,
      points: [...probe.points]
    };
    drawStrokeSegments(this.workCtx as MarkupContext, this.active.points, 0, this.active.color, this.active.width, kind === 'erase');
    this.revisionCounter += 1;
    return true;
  }

  extendStroke(point: MarkupPoint): boolean {
    this.assertAlive();
    const stroke = this.active;
    if (!stroke || !finite(point.x) || !finite(point.y)) return false;
    const x = clampNumber(point.x, 0, this.width);
    const y = clampNumber(point.y, 0, this.height);
    const last = stroke.points.length;
    if (stroke.points[last - 2] === x && stroke.points[last - 1] === y) return false;
    stroke.points.push(x, y);
    drawStrokeSegments(
      this.workCtx as MarkupContext,
      stroke.points,
      stroke.points.length / 2 - 1,
      stroke.color,
      stroke.width,
      stroke.kind === 'erase'
    );
    this.revisionCounter += 1;
    return true;
  }

  endStroke(): boolean {
    this.assertAlive();
    const stroke = this.active;
    if (!stroke) return false;
    this.active = null;
    this.push(
      stroke.kind === 'pen'
        ? { kind: 'pen', color: stroke.color, width: stroke.width, points: stroke.points }
        : { kind: 'erase', width: stroke.width, points: stroke.points }
    );
    this.revisionCounter += 1;
    return true;
  }

  /** Discards an unfinished stroke (second touch, capture loss, Escape) without recording it. */
  cancelStroke(): boolean {
    if (this.disposed || !this.active) return false;
    this.active = null;
    this.rebuild();
    return true;
  }

  undo(): boolean {
    if (!this.canUndo) return false;
    this.cursor -= 1;
    this.rebuild();
    return true;
  }

  redo(): boolean {
    if (!this.canRedo) return false;
    renderMarkupCommand(this.workCtx as MarkupContext, this.commands[this.cursor], this.size);
    this.cursor += 1;
    this.revisionCounter += 1;
    return true;
  }

  /**
   * Streams the layer in row bands, so a 16-megapixel layer is never copied
   * out in one piece.
   */
  analyze(): MarkupLayerStats {
    this.assertAlive();
    const ctx = this.workCtx as MarkupContext;
    const bandRows = Math.max(1, Math.min(this.height, Math.floor(1_048_576 / this.width)));
    let nonEmpty = false;
    let hasTransparency = false;
    for (let y = 0; y < this.height; y += bandRows) {
      const rows = Math.min(bandRows, this.height - y);
      const pixels = ctx.getImageData(0, y, this.width, rows).data;
      for (let i = 3; i < pixels.length; i += 4) {
        const alpha = pixels[i];
        if (alpha > 0) nonEmpty = true;
        if (alpha < 255) hasTransparency = true;
        if (nonEmpty && hasTransparency) return { nonEmpty, hasTransparency };
      }
    }
    return { nonEmpty, hasTransparency };
  }

  private async currentBaseBlob(): Promise<Blob | null> {
    if (!this.base) return null;
    if (this.baseBlob && !this.baseBlobStale) return this.baseBlob;
    const blob = await canvasToBlob(this.base);
    this.baseBlob = blob;
    this.baseBlobStale = false;
    return blob;
  }

  /**
   * Produces the upload PNG (annotations only, native size, original alpha) and
   * the matching immutable editor state. Fails loudly when the layer is empty or
   * fully opaque instead of silently returning something else.
   */
  async exportApplied(): Promise<MarkupExport> {
    this.assertAlive();
    if (this.active) throw new MarkupDocumentError('busy');
    const revision = this.revisionCounter;
    const stats = this.analyze();
    if (!stats.nonEmpty) throw new MarkupDocumentError('empty');
    if (!stats.hasTransparency) throw new MarkupDocumentError('opaque');
    const commands = this.commands.slice(0, this.cursor);
    const [blob, baseBlob] = await Promise.all([canvasToBlob(this.work as MarkupCanvasLike), this.currentBaseBlob()]);
    if (this.disposed) throw new MarkupDocumentError('disposed');
    if (revision !== this.revisionCounter) throw new MarkupDocumentError('stale');
    return {
      blob,
      width: this.width,
      height: this.height,
      state: Object.freeze({
        width: this.width,
        height: this.height,
        base: baseBlob,
        commands: Object.freeze(commands)
      })
    };
  }

  /** Releases canvas backing stores right away instead of waiting for GC. */
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.active = null;
    if (this.work) {
      this.work.width = 0;
      this.work.height = 0;
    }
    if (this.base) {
      this.base.width = 0;
      this.base.height = 0;
    }
    this.work = null;
    this.workCtx = null;
    this.base = null;
    this.baseBlob = null;
    this.commands = [];
    this.cursor = 0;
  }
}
