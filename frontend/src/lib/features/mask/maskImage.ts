/**
 * PNG and alpha helpers shared by the mask editor and the workspace.
 *
 * Everything here works on bytes or a throwaway canvas, and none of it touches
 * the painting document. That split is deliberate: the workspace measures a
 * restored mask blob long before the editor is ever opened, and importing the
 * document for that would drag the whole editor (and its worker plumbing) into
 * the homepage bundle.
 */

export const MAX_MASK_FILE_BYTES = 4 * 1024 * 1024;

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const ALPHA_CHANNEL_COLOR_TYPES = new Set([4, 6]);
const PALETTE_COLOR_TYPE = 3;

/** Alpha at or above this is a marked pixel (matches the export's binarize). */
export const MARK_ALPHA_THRESHOLD = 128;

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

/** Create a detached canvas of the given size. */
export function createCanvas(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

export function context2d(canvas: HTMLCanvasElement, readFrequently = false): CanvasRenderingContext2D {
  const context = canvas.getContext('2d', readFrequently ? { willReadFrequently: true } : undefined);
  if (!context) throw new Error('Canvas 2D context is unavailable');
  return context;
}

export async function decodeImage(file: Blob): Promise<CanvasImageSource & { width: number; height: number }> {
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

/**
 * Binarize the exported mask alpha in place (alpha < 128 becomes 0 = editable,
 * everything else becomes 255 = keep) and return the editable-area ratio.
 */
export function binarizeMaskAlphaData(data: Uint8ClampedArray): number {
  const total = data.length / 4;
  if (!total) return 0;
  let transparent = 0;
  for (let index = 3; index < data.length; index += 4) {
    if (data[index] < MARK_ALPHA_THRESHOLD) {
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
    if (data[index] >= MARK_ALPHA_THRESHOLD) marked += 1;
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
