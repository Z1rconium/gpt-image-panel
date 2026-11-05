import { apiFetch, apiFetchRaw, DEFAULT_API_TIMEOUT_MS } from '$lib/api/client';
import { primaryEditSourceId, type EditSourceState } from '$lib/stores/editSource';

// One place that turns the current primary edit source into its original,
// upright pixels. The markup validator and the markup editor both go through
// here so they cannot disagree about EXIF orientation, CDN previews or size.
//
// - Uploads decode the File the user picked.
// - Gallery sources always decode the authenticated same-origin original
//   (/api/image/<filename>), never `galleryPreviewUrl`, which may be a CDN
//   thumbnail with different dimensions. Gallery size metadata can also hold
//   raw (pre-EXIF) dimensions, so it is never used as the coordinate basis.

export class PrimaryImageError extends Error {
  constructor(message = 'primary image unavailable') {
    super(message);
    this.name = 'PrimaryImageError';
  }
}

export type PrimaryImageBlob = { sourceId: string; blob: Blob };
export type PrimaryImage = PrimaryImageBlob & { bitmap: ImageBitmap; width: number; height: number };

function withTimeout(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(DEFAULT_API_TIMEOUT_MS);
  if (!signal) return timeout;
  return typeof AbortSignal.any === 'function' ? AbortSignal.any([signal, timeout]) : signal;
}

export async function fetchPrimaryImageBlob(state: EditSourceState, signal?: AbortSignal): Promise<PrimaryImageBlob> {
  const sourceId = primaryEditSourceId(state);
  if (!sourceId) throw new PrimaryImageError();
  if (state.selectedGalleryImageId) {
    const filename =
      state.galleryFilename ||
      (
        await apiFetch<{ filename: string }>(
          `/api/gallery/${encodeURIComponent(state.selectedGalleryImageId)}`,
          { signal },
          'loading markup primary image'
        )
      ).filename;
    const response = await apiFetchRaw(`/api/image/${encodeURIComponent(filename)}`, { signal: withTimeout(signal) });
    if (!response.ok) throw new PrimaryImageError();
    return { sourceId, blob: await response.blob() };
  }
  const primary = state.files[0];
  if (!primary) throw new PrimaryImageError();
  return { sourceId, blob: primary.file };
}

/** Decodes with default orientation handling, i.e. the upright image the backend sends upstream. */
export async function decodePrimaryBlob(blob: Blob): Promise<{ bitmap: ImageBitmap; width: number; height: number }> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(blob);
  } catch {
    throw new PrimaryImageError('primary image could not be decoded');
  }
  if (!bitmap.width || !bitmap.height) {
    bitmap.close();
    throw new PrimaryImageError('primary image has no size');
  }
  return { bitmap, width: bitmap.width, height: bitmap.height };
}

/** Upright dimensions only; the bitmap is released immediately. */
export async function readPrimaryDimensions(state: EditSourceState, signal?: AbortSignal) {
  const { sourceId, blob } = await fetchPrimaryImageBlob(state, signal);
  const decoded = await decodePrimaryBlob(blob);
  decoded.bitmap.close();
  return { sourceId, width: decoded.width, height: decoded.height };
}

/**
 * Loads the full primary image for editing. If the caller aborts while the
 * fetch or decode is in flight, the late bitmap is closed and the promise
 * rejects, so stale results can never be attached to a newer session.
 */
export async function loadPrimaryImage(state: EditSourceState, signal?: AbortSignal): Promise<PrimaryImage> {
  const { sourceId, blob } = await fetchPrimaryImageBlob(state, signal);
  const decoded = await decodePrimaryBlob(blob);
  if (signal?.aborted) {
    decoded.bitmap.close();
    throw new DOMException('Aborted', 'AbortError');
  }
  return { sourceId, blob, ...decoded };
}
