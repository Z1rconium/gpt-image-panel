/**
 * Mask painting document.
 *
 * The data layer is a natural-resolution canvas where any painted alpha counts
 * as "edit this region"; the upstream contract only looks at fully transparent
 * pixels of an exported PNG, so `exportPng` renders black + destination-out and
 * then binarizes alpha to 0/255.
 *
 * Pure helpers live at the top so they can be unit-tested without a canvas.
 */

export const MAX_MASK_FILE_BYTES = 4 * 1024 * 1024;
export const MAX_SMOOTH_PX = 64;

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

/** Ratio of fully marked pixels on the marks canvas (alpha >= 128). */
export function countMarkedPixels(data: Uint8ClampedArray): number {
  const total = data.length / 4;
  if (!total) return 0;
  let marked = 0;
  for (let index = 3; index < data.length; index += 4) {
    if (data[index] >= 128) marked += 1;
  }
  return marked / total;
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
  layer: HTMLCanvasElement;
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

function context2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext('2d');
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
  const context = canvas.getContext('2d');
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
  const context = context2d(marks);
  const scratch = createCanvas(width, height);
  const scratchContext = context2d(scratch);
  // Processed view of the marks (edge-smoothed and re-binarized) used by the
  // canvas preview, the coverage readout and the export so all three share one
  // rendering rule.
  const processed = createCanvas(width, height);
  const blurLayer = createCanvas(width, height);

  let commands: Command[] = [];
  let redoStack: Command[] = [];
  let activeStroke: StrokeCommand | null = null;
  let activeShape: ShapeCommand | null = null;
  let smoothPx = 0;
  let processedStale = false;

  function strokeContext(target: CanvasRenderingContext2D) {
    target.save();
    target.globalCompositeOperation = 'source-over';
    target.strokeStyle = '#000';
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
    fillStyle = '#000'
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
    scratchContext.save();
    scratchContext.globalCompositeOperation = 'source-over';
    scratchContext.clearRect(0, 0, width, height);
    scratchContext.fillStyle = '#000';
    scratchContext.fillRect(0, 0, width, height);
    scratchContext.globalCompositeOperation = 'destination-out';
    scratchContext.drawImage(marks, 0, 0);
    scratchContext.restore();

    context.save();
    context.globalCompositeOperation = 'copy';
    context.clearRect(0, 0, width, height);
    context.drawImage(scratch, 0, 0);
    context.restore();
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
      case 'import':
        context.save();
        // A replacing import wipes everything before drawing, so undo/redo
        // replay stays a single command and still restores the previous state.
        context.globalCompositeOperation = command.replace ? 'copy' : 'source-over';
        context.drawImage(command.layer, 0, 0);
        context.restore();
        break;
      case 'clear':
        context.clearRect(0, 0, width, height);
        break;
      case 'invert':
        invertMarks();
        break;
    }
  }

  function replay() {
    context.save();
    context.globalCompositeOperation = 'copy';
    context.clearRect(0, 0, width, height);
    context.restore();
    commands.forEach(applyCommand);
  }

  function coverage() {
    const imageData = context.getImageData(0, 0, width, height);
    return countMarkedPixels(imageData.data);
  }

  function invalidateProcessed() {
    processedStale = true;
  }

  // Blur the marks and re-binarize the alpha: the same contour the export
  // produces, so the on-canvas preview never promises a different region.
  function repaintProcessed() {
    const target = context2d(processed);
    target.save();
    target.globalCompositeOperation = 'copy';
    target.clearRect(0, 0, width, height);
    if (smoothPx > 0) {
      const blur = context2d(blurLayer);
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
      target.drawImage(blurLayer, 0, 0);
    } else {
      target.drawImage(marks, 0, 0);
    }
    target.restore();
    processedStale = false;
  }

  /**
   * Edited-area ratio of the processed view. The processed canvas keeps the
   * marks orientation (painted alpha = editable), unlike the exported PNG
   * where editable pixels end up fully transparent.
   */
  function processedEditableRatio() {
    const data = context2d(processed).getImageData(0, 0, width, height).data;
    return countMarkedPixels(data);
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
      drawStroke(context, activeStroke, fromIndex);
      invalidateProcessed();
    },

    endStroke() {
      if (activeShape) {
        const command = activeShape;
        activeShape = null;
        if (fillShape(context, command)) {
          commands.push(command);
          redoStack = [];
          invalidateProcessed();
        }
      }
      activeStroke = null;
      return coverage();
    },

    canUndo() {
      return commands.length > 0;
    },

    canRedo() {
      return redoStack.length > 0;
    },

    undo() {
      const command = commands.pop();
      if (!command) return null;
      redoStack.push(command);
      replay();
      invalidateProcessed();
      return coverage();
    },

    redo() {
      const command = redoStack.pop();
      if (!command) return null;
      commands.push(command);
      applyCommand(command);
      invalidateProcessed();
      return coverage();
    },

    clear() {
      commands.push({ kind: 'clear' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      context.clearRect(0, 0, width, height);
      invalidateProcessed();
      return 0;
    },

    invert() {
      commands.push({ kind: 'invert' });
      redoStack = [];
      activeStroke = null;
      activeShape = null;
      invertMarks();
      invalidateProcessed();
      return coverage();
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

      const layer = createCanvas(width, height);
      const layerContext = layer.getContext('2d');
      if (!layerContext) throw new MaskImportError('decode');
      layerContext.drawImage(decoded, 0, 0);
      if (typeof ImageBitmap !== 'undefined' && decoded instanceof ImageBitmap) decoded.close();

      const imageData = layerContext.getImageData(0, 0, width, height);
      const data = imageData.data;
      for (let index = 0; index < data.length; index += 4) {
        const editable = data[index + 3] === 0;
        data[index] = 0;
        data[index + 1] = 0;
        data[index + 2] = 0;
        data[index + 3] = editable ? 255 : 0;
      }
      layerContext.putImageData(imageData, 0, 0);

      const command: ImportCommand = { kind: 'import', layer, replace: mode === 'replace' };
      commands.push(command);
      redoStack = [];
      applyCommand(command);
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
      return !processedStale;
    },

    get smoothPx() {
      return smoothPx;
    },

    processed,

    coverage,

    hasActiveShape() {
      return activeShape !== null;
    },

    /** Draw the in-progress rect/lasso into a display-sized context. */
    drawActiveShapePreview(
      target: CanvasRenderingContext2D,
      targetWidth: number,
      targetHeight: number
    ) {
      if (!activeShape) return;
      target.save();
      target.scale(targetWidth / width, targetHeight / height);
      fillShape(
        target,
        activeShape,
        activeShape.mode === 'erase' ? '#000' : 'rgba(16, 185, 129, 0.45)'
      );
      target.restore();
    },

    async exportPng(options: { smoothing?: number } = {}) {
      const smoothing = normalizeSmoothPx(options.smoothing ?? smoothPx, width);
      const canvas = createCanvas(width, height);
      const exportContext = canvas.getContext('2d');
      if (!exportContext) throw new MaskImportError('decode');
      exportContext.fillStyle = '#000';
      exportContext.fillRect(0, 0, width, height);
      exportContext.globalCompositeOperation = 'destination-out';

      // The upstream contract only edits alpha == 0 pixels, so a soft gradient
      // cannot survive the round trip. Blurring before binarizing only smooths
      // the contour (removes pointer jaggies) while keeping the region binary —
      // the exact rule the on-canvas preview and coverage readout use.
      if (smoothing > 0) {
        const blurred = createCanvas(width, height);
        const blurredContext = blurred.getContext('2d');
        if (!blurredContext) throw new MaskImportError('decode');
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
      marks.width = 0;
      marks.height = 0;
      scratch.width = 0;
      scratch.height = 0;
      processed.width = 0;
      processed.height = 0;
      blurLayer.width = 0;
      blurLayer.height = 0;
    }
  };
}
