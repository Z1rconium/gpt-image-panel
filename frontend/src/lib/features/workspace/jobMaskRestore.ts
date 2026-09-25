import { get } from 'svelte/store';
import { ApiError, DEFAULT_API_TIMEOUT_MS } from '$lib/api/client';
import { t } from '$lib/i18n';

export type JobMaskFetch = {
  blob: Blob;
  /** From the X-Mask-Coverage response header; null if the server omitted it. */
  coverage: number | null;
};

/** Verify the current primary before reading the stored mask and coverage. */
export async function fetchJobMaskBlob(jobId: string, source: { file: File } | { galleryImageId: string }): Promise<JobMaskFetch> {
  const form = new FormData();
  if ('file' in source) {
    const digest = await crypto.subtle.digest('SHA-256', await source.file.arrayBuffer());
    form.set('source_sha256', Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join(''));
  } else {
    form.set('gallery_image_id', source.galleryImageId);
  }
  let response: Response;
  try {
    response = await fetch(`/api/generate/${encodeURIComponent(jobId)}/mask/restore`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'image/png' },
      body: form,
      signal: AbortSignal.timeout(DEFAULT_API_TIMEOUT_MS)
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : get(t).messages.failedToFetch;
    throw new Error(get(t).messages.networkError(message));
  }
  if (!response.ok) {
    const message = response.status === 404 ? get(t).messages.editRetryMaskMissing : get(t).messages.requestFailed;
    throw new ApiError(message, response.status, null, 'loading job mask');
  }
  const coverageHeader = response.headers.get('X-Mask-Coverage');
  const coverage = coverageHeader !== null ? Number(coverageHeader) : null;
  return {
    blob: await response.blob(),
    coverage: coverage !== null && Number.isFinite(coverage) ? coverage : null
  };
}
