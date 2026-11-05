import { get } from 'svelte/store';
import { settingsStore } from '$lib/stores/settings';
import { t } from '$lib/i18n';
import {
  editSourceStore,
  primaryEditSourceId,
  type EditMarkupOrigin
} from '$lib/stores/editSource';
// Type-only: the drawing implementation stays out of the homepage graph.
import type { MarkupEditorState } from '$lib/features/markup/markupDocument';
import { readPrimaryDimensions } from '$lib/features/workspace/editPrimaryImage';

const MAX_MARKUP_BYTES = 50 * 1024 * 1024;
const MAX_MARKUP_PIXELS = 16_777_216;
const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];

/** What the editor hands to the workspace when the user presses "Apply markup". */
export type MarkupApplyPayload = {
  sessionId: number;
  sourceId: string;
  blob: Blob;
  width: number;
  height: number;
  editorState: MarkupEditorState;
  origin: EditMarkupOrigin;
};

export type MarkupRejection =
  | 'file_size'
  | 'not_png'
  | 'pixels'
  | 'decode'
  | 'size_mismatch'
  | 'opaque'
  | 'source';

export class MarkupRejectedError extends Error {
  readonly reason: MarkupRejection;
  constructor(reason: MarkupRejection) {
    super(reason);
    this.name = 'MarkupRejectedError';
    this.reason = reason;
  }
}

export function markupLimits() {
  const upload = get(settingsStore).settings?.image_upload_limits;
  return {
    maxBytes: upload?.max_file_size_bytes ?? Number.POSITIVE_INFINITY,
    maxPixels: Math.min(MAX_MARKUP_PIXELS, upload?.max_image_pixels ?? MAX_MARKUP_PIXELS)
  };
}

/** PNG signature plus IHDR width/height, read without decoding. */
async function readPngHeader(blob: Blob): Promise<{ width: number; height: number } | null> {
  const bytes = new Uint8Array(await blob.slice(0, 24).arrayBuffer());
  if (bytes.length < 24 || PNG_SIGNATURE.some((value, index) => bytes[index] !== value)) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

function assertMarkupSize(blob: Blob, width: number, height: number) {
  const limits = markupLimits();
  if (blob.size >= MAX_MARKUP_BYTES || blob.size > limits.maxBytes) throw new MarkupRejectedError('file_size');
  if (!width || !height || width * height > limits.maxPixels) throw new MarkupRejectedError('pixels');
}

/**
 * Checks a PNG produced by the in-page editor against the same size limits as
 * an uploaded one. Transparency was already verified on the exact canvas that
 * was encoded, and the header check proves the bytes really are that size.
 */
export async function validatePaintedMarkup(blob: Blob, width: number, height: number): Promise<void> {
  assertMarkupSize(blob, width, height);
  const header = await readPngHeader(blob);
  if (!header) throw new MarkupRejectedError('not_png');
  if (header.width !== width || header.height !== height) throw new MarkupRejectedError('size_mismatch');
}

/** Scans in row bands so a large layer is never read out in one piece. */
function hasTransparentPixel(bitmap: ImageBitmap): boolean {
  const { width, height } = bitmap;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new MarkupRejectedError('decode');
  try {
    context.drawImage(bitmap, 0, 0);
    const bandRows = Math.max(1, Math.min(height, Math.floor(1_048_576 / width)));
    for (let y = 0; y < height; y += bandRows) {
      const pixels = context.getImageData(0, y, width, Math.min(bandRows, height - y)).data;
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i] < 255) return true;
    }
    return false;
  } finally {
    canvas.width = 0;
    canvas.height = 0;
  }
}

export type InstallMarkupInput = {
  sourceId: string;
  blob: Blob;
  width: number;
  height: number;
  origin: EditMarkupOrigin;
  editorState?: MarkupEditorState;
};

/**
 * The single point where a validated markup becomes the applied one. It runs
 * synchronously: the primary-source check and the store swap cannot be
 * interleaved with any other store update, and the previous markup's object URL
 * is released only by the swap itself.
 */
export function installMarkup(input: InstallMarkupInput, stillCurrent?: () => boolean): void {
  if (primaryEditSourceId(get(editSourceStore)) !== input.sourceId || stillCurrent?.() === false) {
    throw new MarkupRejectedError('source');
  }
  editSourceStore.setMarkup({
    sourceId: input.sourceId,
    blob: input.blob,
    width: input.width,
    height: input.height,
    previewUrl: URL.createObjectURL(input.blob),
    origin: input.origin,
    editorState: input.editorState
  });
}

/** Localised, specific reason for a rejected markup (editor flow). */
export function markupRejectionMessage(error: unknown): string {
  const copy = get(t).markupEditor;
  if (error instanceof MarkupRejectedError) {
    switch (error.reason) {
      case 'file_size':
        return copy.errorFileSize;
      case 'pixels':
        return copy.errorPixels;
      case 'source':
        return copy.errorSourceChanged;
      case 'opaque':
        return copy.errorOpaque;
      default:
        return copy.errorInvalid;
    }
  }
  return copy.errorExport;
}

/**
 * Uploads / restores a PNG as the markup for the current primary image.
 * Existing contract: any failure surfaces as the generic `invalidMarkup` text,
 * and a failure never touches the markup that is already applied.
 */
export async function attachMarkup(blob: Blob, options: { origin?: EditMarkupOrigin } = {}): Promise<void> {
  const state = get(editSourceStore);
  const sourceId = primaryEditSourceId(state);
  const fail = () => new Error(get(t).promptForm.invalidMarkup);
  let bitmap: ImageBitmap | undefined;
  try {
    const header = await readPngHeader(blob);
    if (!sourceId || !header) throw new MarkupRejectedError('not_png');
    assertMarkupSize(blob, header.width, header.height);
    bitmap = await createImageBitmap(blob);
    const { width, height } = bitmap;
    assertMarkupSize(blob, width, height);
    // Gallery metadata records raw file dimensions; the shared helper decodes
    // the original so EXIF-rotated photos compare as the upright image the
    // backend sends upstream.
    const primary = await readPrimaryDimensions(state);
    if (width !== primary.width || height !== primary.height) throw new MarkupRejectedError('size_mismatch');
    if (!hasTransparentPixel(bitmap)) throw new MarkupRejectedError('opaque');
    installMarkup({ sourceId, blob, width, height, origin: options.origin ?? 'uploaded' });
  } catch {
    throw fail();
  } finally {
    bitmap?.close();
  }
}
