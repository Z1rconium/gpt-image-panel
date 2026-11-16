import { get } from 'svelte/store';
import { ApiError, DEFAULT_API_TIMEOUT_MS, apiFetchRaw } from '$lib/api/client';
import { generateJobMaskRestoreUrl } from '$lib/api/jobs';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import {
  editSourceStore,
  primaryEditSource,
  primaryEditSourceId,
  primaryRestoreSource,
  type EditMask,
  type EditSourceState
} from '$lib/stores/editSource';
import { uiStore } from '$lib/stores/ui';
import {
  installMarkup,
  markupRejectionMessage,
  MarkupRejectedError,
  validatePaintedMarkup,
  type MarkupApplyPayload
} from '$lib/features/workspace/markup';
// Type-only: keep maskImage helpers out of the homepage graph until mask restore runs.
import type { MaskMeasurements } from '$lib/features/mask/maskImage';

export type JobMaskFetch = {
  blob: Blob;
  /** From the X-Mask-Coverage response header; null if the server omitted it. */
  coverage: number | null;
};

export type MaskRestoreOutcome = 'restored' | 'missing' | 'mismatch';

export type MaskImageHelpers = Pick<
  typeof import('$lib/features/mask/maskImage'),
  'readPngSize' | 'measureMaskBlob'
>;

const defaultMessages = () => get(t);
export type EditSessionMessages = ReturnType<typeof defaultMessages>;

export type EditSessionDeps = {
  store?: Pick<
    typeof editSourceStore,
    'subscribe' | 'setMask' | 'setMarkup' | 'removeMask' | 'removeMarkup'
  >;
  confirm?: typeof confirmStore.confirm;
  toast?: (message: string, variant?: 'status' | 'error') => void;
  fetchRaw?: typeof apiFetchRaw;
  loadMaskHelpers?: () => Promise<MaskImageHelpers>;
  validateMarkup?: typeof validatePaintedMarkup;
  installMarkupFn?: typeof installMarkup;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
  messages?: () => EditSessionMessages;
};

export type PaintedMarkupApplyOptions = {
  expectedMarkupRef: string | null;
  isEditorCurrent: () => boolean;
  onApplied?: () => void;
};

/** Verify the current primary before reading the stored mask/markup and coverage. */
export async function fetchJobMaskBlob(
  jobId: string,
  source: { file: File } | { galleryImageId: string },
  kind: 'mask' | 'markup' = 'mask',
  deps: { fetchRaw?: typeof apiFetchRaw; messages?: () => EditSessionMessages } = {}
): Promise<JobMaskFetch> {
  const fetchRaw = deps.fetchRaw ?? apiFetchRaw;
  const messages = deps.messages ?? defaultMessages;
  const form = new FormData();
  if ('file' in source) {
    const digest = await crypto.subtle.digest('SHA-256', await source.file.arrayBuffer());
    form.set(
      'source_sha256',
      Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
    );
  } else {
    form.set('gallery_image_id', source.galleryImageId);
  }
  let response: Response;
  try {
    response = await fetchRaw(generateJobMaskRestoreUrl(jobId, kind), {
      method: 'POST',
      headers: { Accept: 'image/png' },
      body: form,
      timeoutMs: DEFAULT_API_TIMEOUT_MS
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : messages().messages.failedToFetch;
    throw new Error(messages().messages.networkError(message));
  }
  if (!response.ok) {
    const message =
      response.status === 404 ? messages().messages.editRetryMaskMissing : messages().messages.requestFailed;
    throw new ApiError(message, response.status, null, 'loading job mask');
  }
  const coverageHeader = response.headers.get('X-Mask-Coverage');
  const coverage = coverageHeader !== null ? Number(coverageHeader) : null;
  return {
    blob: await response.blob(),
    coverage: coverage !== null && Number.isFinite(coverage) ? coverage : null
  };
}

/**
 * Coordinates edit-source mask/markup restore and apply flows outside Svelte
 * components, including confirmation dialogs and post-await primary re-checks.
 */
export function createEditSession(deps: EditSessionDeps = {}) {
  const store = deps.store ?? editSourceStore;
  const confirm = deps.confirm ?? confirmStore.confirm;
  const toast = deps.toast ?? ((message, variant) => uiStore.showToast(message, variant));
  const fetchRaw = deps.fetchRaw ?? apiFetchRaw;
  const loadMaskHelpers = deps.loadMaskHelpers ?? (() => import('$lib/features/mask/maskImage'));
  const validateMarkup = deps.validateMarkup ?? validatePaintedMarkup;
  const installMarkupFn = deps.installMarkupFn ?? installMarkup;
  const createObjectURL = deps.createObjectURL ?? ((blob: Blob) => URL.createObjectURL(blob));
  const revokeObjectURL = deps.revokeObjectURL ?? ((url: string) => URL.revokeObjectURL(url));
  const messages = deps.messages ?? defaultMessages;

  function getState(): EditSourceState {
    return get(store);
  }

  async function confirmReplaceLayer(replacing: 'mask' | 'markup'): Promise<boolean> {
    const state = getState();
    if (replacing === 'markup' && !state.markup) return true;
    if (replacing === 'mask' && !state.mask) return true;
    const copy = messages();
    return confirm({
      title: replacing === 'markup' ? copy.markupEditor.replaceMarkupTitle : copy.markupEditor.replaceMaskTitle,
      message: replacing === 'markup' ? copy.markupEditor.replaceMarkupMessage : copy.markupEditor.replaceMaskMessage,
      confirmLabel:
        replacing === 'markup' ? copy.markupEditor.replaceMarkupConfirm : copy.markupEditor.replaceMaskConfirm,
      cancelLabel: copy.confirm.cancel,
      closeLabel: copy.confirm.closeLabel,
      variant: 'default'
    });
  }

  async function restoreMaskFromJob(job: GenerateJobStatus): Promise<MaskRestoreOutcome> {
    const initialState = getState();
    const sourceId = primaryEditSourceId(initialState);
    if (!sourceId) return 'missing';
    const restoreSource = primaryRestoreSource(initialState);
    if (!restoreSource) return 'missing';

    let fetched: JobMaskFetch;
    let maskHelpers: MaskImageHelpers;
    try {
      [fetched, maskHelpers] = await Promise.all([
        fetchJobMaskBlob(job.job_id, restoreSource, 'mask', { fetchRaw, messages }),
        loadMaskHelpers()
      ]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) return 'mismatch';
      return 'missing';
    }
    const { blob } = fetched;

    // The server already validated this mask and knows its coverage (see
    // X-Mask-Coverage on GET .../mask), and its size is a plain IHDR read —
    // neither needs a canvas decode. Only fall back to the full decode in
    // measureMaskBlob() when either fast path comes back empty (an older
    // server build, or an unreadable/never-PNG response).
    let measurements: MaskMeasurements;
    const fastSize =
      fetched.coverage !== null
        ? maskHelpers.readPngSize(new Uint8Array(await blob.arrayBuffer()))
        : null;
    if (fastSize && fetched.coverage !== null) {
      measurements = { width: fastSize.width, height: fastSize.height, coverage: fetched.coverage };
    } else {
      try {
        measurements = await maskHelpers.measureMaskBlob(blob);
      } catch {
        return 'missing';
      }
    }

    // Re-check primary identity and upright dimensions after all awaits so a
    // source swap while restoring never attaches a mask to the wrong image.
    const latestState = getState();
    if (primaryEditSourceId(latestState) !== sourceId) return 'mismatch';
    const primary = primaryEditSource(latestState);
    if (
      primary &&
      primary.width > 0 &&
      primary.height > 0 &&
      (primary.width !== measurements.width || primary.height !== measurements.height)
    ) {
      return 'mismatch';
    }

    store.setMask({
      sourceId,
      blob,
      width: measurements.width,
      height: measurements.height,
      coverage: measurements.coverage,
      previewUrl: createObjectURL(blob),
      origin: 'restored'
    });
    toast(messages().messages.editMaskRestored((measurements.coverage * 100).toFixed(1)));
    return 'restored';
  }

  async function applyEditMask(mask: EditMask, options: { onApplied?: () => void } = {}): Promise<boolean> {
    // A request carries a mask or a markup layer, never both. Applying a mask
    // over an existing markup is confirmed; declining keeps both untouched.
    if (getState().markup) {
      const replace = await confirmReplaceLayer('markup');
      if (!replace || primaryEditSourceId(getState()) !== mask.sourceId) {
        revokeObjectURL(mask.previewUrl);
        return false;
      }
    } else if (primaryEditSourceId(getState()) !== mask.sourceId) {
      revokeObjectURL(mask.previewUrl);
      return false;
    }
    store.setMask(mask);
    toast(messages().messages.editMaskApplied((mask.coverage * 100).toFixed(1)));
    options.onApplied?.();
    return true;
  }

  /**
   * Final, atomic step of "Apply markup". Returns true only when the new markup
   * is installed; on any refusal the previous markup, mask and the editor's
   * working copy are left exactly as they were.
   */
  async function applyPaintedMarkup(
    payload: MarkupApplyPayload,
    options: PaintedMarkupApplyOptions
  ): Promise<boolean> {
    const current = () => {
      const state = getState();
      return (
        options.isEditorCurrent() &&
        primaryEditSourceId(state) === payload.sourceId &&
        (state.markup?.previewUrl ?? null) === options.expectedMarkupRef
      );
    };
    if (!current()) return false;
    try {
      await validateMarkup(payload.blob, payload.width, payload.height);
      if (!current()) throw new MarkupRejectedError('source');
      if (getState().mask) {
        const replace = await confirmReplaceLayer('mask');
        if (!replace) return false;
        if (!current()) throw new MarkupRejectedError('source');
      }
      installMarkupFn(
        {
          sourceId: payload.sourceId,
          blob: payload.blob,
          width: payload.width,
          height: payload.height,
          origin: payload.origin,
          editorState: payload.editorState
        },
        current
      );
      toast(messages().markupEditor.applied);
      options.onApplied?.();
      return true;
    } catch (error) {
      toast(markupRejectionMessage(error), 'error');
      return false;
    }
  }

  return {
    fetchJobMaskBlob: (
      jobId: string,
      source: { file: File } | { galleryImageId: string },
      kind: 'mask' | 'markup' = 'mask'
    ) => fetchJobMaskBlob(jobId, source, kind, { fetchRaw, messages }),
    confirmReplaceMask: () => confirmReplaceLayer('mask'),
    restoreMaskFromJob,
    applyEditMask,
    applyPaintedMarkup
  };
}
