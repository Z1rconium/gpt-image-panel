import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { MAX_EDIT_SOURCE_IMAGES, editSourceCount, editSourceStore, isMaskValid, type EditSourceState } from '$lib/stores/editSource';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { GeneratePreviewEvent, GenerateJobResponse, GenerateJobStatus } from '$lib/api/types/jobs';
import { MAX_PROMPT_CHARS, imageQualities, isImage25, promptLength, validImageSize } from '$lib/utils/imageModels';
import { isActiveJobStatus } from '$lib/utils/jobs';

/** Latest streamed partial image for one task unit (keyed by unit_index). */
export type StreamingPreviewSlot = { dataUrl: string; sequence: number; unitIndex?: number; callIndex?: number };

export type PreviewState = {
  loading: boolean;
  error: string;
  job: GenerateJobStatus | null;
  imageUrl: string;
  filename: string;
  prompt: string;
  streamingPreviews: Record<string, StreamingPreviewSlot>;
  compareSource: PreviewCompareSource | null;
};

export type PreviewCompareSource =
  | { kind: 'upload'; file: File; label: string }
  | { kind: 'gallery'; url: string; label: string };

export type PromptFormState = {
  prompt: string;
  apiPath: ApiPath;
  size: string;
  model: string;
  quality: GenerateRequestBody['quality'];
  outputFormat: GenerateRequestBody['output_format'];
  background: NonNullable<GenerateRequestBody['background']>;
  outputCompression: string;
  quantity: number | string;
  responseFormat: ResponseFormatDefault;
  stream: boolean;
  partialImages: number;
  pasteBack: boolean;
};

export const DEFAULT_QUANTITY = 1;

/** Per-submission overrides that are not part of the editable form. */
export type SubmitOptions = {
  /** Pin a specific API preset instead of the active one (job retry). */
  apiPresetId?: string | null;
};

const initialPreviewState: PreviewState = {
  loading: false,
  error: '',
  job: null,
  imageUrl: '',
  filename: '',
  prompt: '',
  streamingPreviews: {},
  compareSource: null
};

export const DEFAULT_PROMPT_MODEL = 'gpt-image-2';

export const initialPromptFormState: PromptFormState = {
  prompt: '',
  apiPath: '/v1/images/generations',
  size: 'auto',
  model: DEFAULT_PROMPT_MODEL,
  quality: 'auto',
  outputFormat: 'png',
  background: 'auto',
  outputCompression: '',
  quantity: DEFAULT_QUANTITY,
  responseFormat: 'url',
  stream: false,
  partialImages: 2,
  pasteBack: true
};

function buildRequestBody(form: PromptFormState): GenerateRequestBody {
  const quantity = form.quantity === '' ? DEFAULT_QUANTITY : Math.min(Math.max(Number(form.quantity) || DEFAULT_QUANTITY, 1), 10);
  const body: GenerateRequestBody = {
    prompt: form.prompt.trim(),
    size: form.size,
    model: form.model.trim(),
    n: quantity,
    quality: form.quality,
    output_format: form.outputFormat,
    output_compression: null,
    background: form.background,
    response_format: !isImage25(form.model) && form.responseFormat ? (form.responseFormat as 'url' | 'b64_json') : null,
    api_path: form.apiPath
  };

  if (form.outputFormat !== 'png' && form.outputCompression !== '') {
    body.output_compression = Math.min(Math.max(Number(form.outputCompression), 0), 100);
  }

  if (body.background === 'chroma_green' || body.background === 'chroma_magenta') {
    body.output_format = 'png';
    body.output_compression = null;
  } else if (body.background === 'transparent' && body.output_format === 'jpeg') {
    body.output_format = 'png';
    body.output_compression = null;
  }

  if (form.stream) {
    body.stream = true;
    body.partial_images = Math.min(Math.max(Math.round(form.partialImages) || 2, 1), 3);
  }

  return body;
}

function submissionError(body: GenerateRequestBody, edit = false): string {
  const messages = get(t);
  if (!body.prompt) return messages.messages.promptRequired;
  if (promptLength(body.prompt) > MAX_PROMPT_CHARS) return messages.promptForm.promptTooLong;
  if (isImage25(body.model) && !edit && body.api_path !== '/v1/images/generations') return messages.promptForm.image25Endpoint;
  if (!imageQualities(body.model).includes(body.quality)) return messages.promptForm.qualityReset;
  if ((edit || body.api_path === '/v1/images/generations') && !validImageSize(body.size)) return messages.sizeDialog.invalidSize;
  if (body.stream && !edit && !['/v1/images/generations', '/v1/responses'].includes(body.api_path || '')) {
    return messages.promptForm.streamUnsupportedPath;
  }
  if (!edit && body.background?.startsWith('chroma_') && body.api_path !== '/v1/images/generations') return messages.promptForm.chromaUnsupportedPath;
  return '';
}

function comparisonSourceForEdit(source: EditSourceState): PreviewCompareSource | null {
  if (!source.mask) return null;
  if (source.selectedGalleryImageId) {
    if (!source.galleryPreviewUrl) return null;
    return { kind: 'gallery', url: source.galleryPreviewUrl, label: source.galleryPreviewLabel || source.galleryLabel };
  }
  const upload = source.files[0];
  // The source store may revoke its preview URL after submission. Keep the
  // File and let PreviewPanel own a separate, short-lived comparison URL.
  return upload ? { kind: 'upload', file: upload.file, label: upload.label } : null;
}

function createPreviewStore() {
  const { subscribe, set, update } = writable<PreviewState>(initialPreviewState);
  let lastRequest: GenerateRequestBody | null = null;
  let lastAction: 'generate' | 'edit' = 'generate';
  let lastPasteBack = true;
  let lastApiPresetId: string | null = null;

  function setPreview(next: PreviewState) {
    set(next);
  }

  function setError(message: string) {
    update((current) => ({ ...current, loading: false, error: message, streamingPreviews: {} }));
  }

  function setSubmissionError(error: unknown) {
    const message = error instanceof Error ? error.message : get(t).messages.requestFailed;
    update((current) => ({ ...current, loading: false, error: message, job: null, compareSource: null, streamingPreviews: {} }));
  }

  function clearPreview(closeActiveJobSource?: () => void) {
    closeActiveJobSource?.();
    set(initialPreviewState);
  }

  function applyPreviewEvent(event: GeneratePreviewEvent) {
    update((current) => {
      if (!current.job || current.job.job_id !== event.job_id) return current;
      // The final image always wins, and out-of-order/duplicate frames
      // (possible across an SSE reconnect) are dropped via the per-unit
      // sequence so one image slot never shows another unit's frame.
      if (!current.loading || !isActiveJobStatus(current.job.status)) return current;
      if (!Number.isInteger(event.unit_index) || event.unit_index < 0 || event.unit_index >= Math.min(current.job.n || 1, 10)) return current;
      const callIndex = event.call_index || 0;
      if (!Number.isInteger(callIndex) || callIndex < 0 || callIndex >= 10 || event.data_url.length > 8 * 1024 * 1024) return current;
      if (current.job.images?.some((image) => image.unit_index === event.unit_index)) return current;
      const unitStatus = current.job.unit_statuses?.[event.unit_index];
      if (unitStatus && !isActiveJobStatus(unitStatus as GenerateJobStatus['status'])) return current;
      const key = callIndex ? `${event.unit_index}:${callIndex}` : `${event.unit_index}`;
      const unit = current.streamingPreviews[key];
      if (unit && event.sequence <= unit.sequence) return current;
      return {
        ...current,
        streamingPreviews: {
          ...current.streamingPreviews,
          [key]: { dataUrl: event.data_url, sequence: event.sequence, unitIndex: event.unit_index, callIndex }
        }
      };
    });
  }

  async function generateImage(
    form: PromptFormState,
    makeQueuedPreview: (prompt: string, operation: NonNullable<GenerateJobResponse['operation']>) => PreviewState,
    trackJob: (jobId: string) => void,
    loadJobs?: () => Promise<void>,
    options: SubmitOptions = {}
  ): Promise<boolean> {
    const body = buildRequestBody(form);
    const error = submissionError(body);
    if (error) {
      setError(error);
      return false;
    }
    if (options.apiPresetId) body.api_preset_id = options.apiPresetId;
    lastRequest = body;
    lastAction = 'generate';
    lastApiPresetId = options.apiPresetId || null;
    set(makeQueuedPreview(body.prompt, 'generation'));

    try {
      const job = await apiFetch<GenerateJobResponse>(
        '/api/generate',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'starting image generation'
      );
      trackJob(job.job_id);
      if (loadJobs) await loadJobs();
      return true;
    } catch (error) {
      setSubmissionError(error);
      return false;
    }
  }

  async function editImage(
    form: PromptFormState,
    editSource: EditSourceState,
    makeQueuedPreview: (prompt: string, operation: NonNullable<GenerateJobResponse['operation']>) => PreviewState,
    trackJob: (jobId: string) => void,
    loadJobs?: () => Promise<void>,
    options: SubmitOptions = {}
  ): Promise<boolean> {
    const sourceCount = editSourceCount(editSource);
    if (sourceCount === 0) {
      setError(get(t).messages.editSourceRequired);
      return false;
    }
    if (sourceCount > MAX_EDIT_SOURCE_IMAGES) {
      setError(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
      return false;
    }
    if (editSource.mask && !isMaskValid(editSource)) {
      setError(get(t).messages.editMaskStale);
      return false;
    }

    const body = buildRequestBody(form);
    if (body.background === 'chroma_green' || body.background === 'chroma_magenta') body.background = 'auto';
    const error = submissionError(body, true);
    if (error) {
      setError(error);
      return false;
    }
    if (options.apiPresetId) body.api_preset_id = options.apiPresetId;
    lastRequest = body;
    lastAction = 'edit';
    lastApiPresetId = options.apiPresetId || null;
    lastPasteBack = form.pasteBack;
    set({ ...makeQueuedPreview(body.prompt, 'edit'), compareSource: comparisonSourceForEdit(editSource) });

    const formData = new FormData();
    Object.entries(body).forEach(([key, value]) => {
      if (key === 'api_path') return;
      if (value !== null && value !== undefined && value !== '') {
        formData.append(key, String(value));
      }
    });

    let endpoint = '/api/edits';
    if (editSource.selectedGalleryImageId) {
      endpoint = `/api/edits/from-gallery/${encodeURIComponent(editSource.selectedGalleryImageId)}`;
    }
    const uploadFieldName = sourceCount > 1 ? 'image[]' : 'image';
    editSource.files.forEach((source) => {
      formData.append(uploadFieldName, source.file, source.file.name);
    });
    if (editSource.mask) {
      formData.append('mask', editSource.mask.blob, 'mask.png');
      formData.append('paste_back', String(form.pasteBack));
    }

    try {
      const job = await apiFetch<GenerateJobResponse>(
        endpoint,
        {
          method: 'POST',
          body: formData
        },
        'starting image edit'
      );
      trackJob(job.job_id);
      if (loadJobs) await loadJobs();
      return true;
    } catch (error) {
      setSubmissionError(error);
      return false;
    }
  }

  function regenerate(
    setForm: (form: PromptFormState) => void,
    generate: (options?: SubmitOptions) => void,
    edit: (options?: SubmitOptions) => void
  ) {
    if (!lastRequest) return;
    setForm({
      prompt: lastRequest.prompt,
      apiPath: lastRequest.api_path || initialPromptFormState.apiPath,
      size: lastRequest.size,
      model: lastRequest.model,
      quantity: lastRequest.n,
      quality: lastRequest.quality,
      outputFormat: lastRequest.output_format,
      background: (lastRequest.background as PromptFormState['background']) ?? 'auto',
      outputCompression: lastRequest.output_compression === null || lastRequest.output_compression === undefined ? '' : String(lastRequest.output_compression),
      responseFormat: lastRequest.response_format || '',
      stream: Boolean(lastRequest.stream),
      partialImages: lastRequest.partial_images ?? initialPromptFormState.partialImages,
      pasteBack: lastPasteBack
    });
    const options = lastApiPresetId ? { apiPresetId: lastApiPresetId } : undefined;
    if (lastAction === 'edit') edit(options);
    else generate(options);
  }

  function cleanup() {
    editSourceStore.cleanup();
    set(initialPreviewState);
  }

  return {
    subscribe,
    setPreview,
    setError,
    clearPreview,
    applyPreviewEvent,
    generateImage,
    editImage,
    regenerate,
    cleanup
  };
}

export const previewStore = createPreviewStore();
