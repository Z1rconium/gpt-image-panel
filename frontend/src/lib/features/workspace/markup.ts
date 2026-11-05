import { apiFetch, apiFetchRaw, DEFAULT_API_TIMEOUT_MS } from '$lib/api/client';
import { get } from 'svelte/store';
import { settingsStore } from '$lib/stores/settings';
import { t } from '$lib/i18n';
import { editSourceStore, primaryEditSourceId } from '$lib/stores/editSource';

export async function attachMarkup(blob: Blob): Promise<void> {
  const state = get(editSourceStore);
  const sourceId = primaryEditSourceId(state);
  const fail = () => new Error(get(t).promptForm.invalidMarkup);
  const bytes = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
  const limit = get(settingsStore).settings?.image_upload_limits;
  if (limit && (blob.size > limit.max_file_size_bytes)) throw fail();
  if (!sourceId || blob.size >= 50 * 1024 * 1024 || bytes.join(',') !== '137,80,78,71,13,10,26,10') throw fail();
  let bitmap: ImageBitmap | undefined;
  try {
    bitmap = await createImageBitmap(blob);
    const { width, height } = bitmap;
    if (!width || !height || width * height > Math.min(16777216, limit?.max_image_pixels ?? 16777216)) throw fail();
    // Gallery metadata records raw file dimensions; decode the original to
    // match the upright EXIF-oriented input the backend sends upstream.
    let primaryBlob: Blob;
    if (state.selectedGalleryImageId) {
      const filename = state.galleryFilename || (await apiFetch<{ filename: string }>(
        `/api/gallery/${encodeURIComponent(state.selectedGalleryImageId)}`, {}, 'loading markup primary image'
      )).filename;
      const response = await apiFetchRaw(`/api/image/${encodeURIComponent(filename)}`, { timeoutMs: DEFAULT_API_TIMEOUT_MS });
      if (!response.ok) throw fail();
      primaryBlob = await response.blob();
    } else {
      const primary = state.files[0];
      if (!primary) throw fail();
      primaryBlob = primary.file;
    }
    const decoded = await createImageBitmap(primaryBlob);
    const primaryWidth = decoded.width;
    const primaryHeight = decoded.height;
    decoded.close();
    if (width !== primaryWidth || height !== primaryHeight) throw fail();
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw fail();
    context.drawImage(bitmap, 0, 0);
    const pixels = context.getImageData(0, 0, width, height).data;
    let transparent = false;
    for (let i = 3; i < pixels.length; i += 4) if (pixels[i] < 255) { transparent = true; break; }
    if (!transparent || primaryEditSourceId(get(editSourceStore)) !== sourceId) throw fail();
    editSourceStore.setMarkup({ sourceId, blob, width, height, previewUrl: URL.createObjectURL(blob) });
  } catch { throw fail(); } finally { bitmap?.close(); }
}
