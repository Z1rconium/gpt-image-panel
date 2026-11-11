import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import {
  MAX_EDIT_SOURCE_IMAGES,
  editSourceCount,
  editSourceStore,
  galleryPrimarySource,
  galleryReferenceSources,
  isMaskValid,
  primaryEditSource,
  uploadSources,
  type EditSourceState
} from '$lib/stores/editSource';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { GeneratePreviewEvent, GenerateJobResponse, GenerateJobStatus } from '$lib/api/types/jobs';
import { jobFailureMessage } from '$lib/utils/format';
import { MAX_PROMPT_CHARS, imageQualities, isImage25, isImage26, promptLength, validOutputSize } from '$lib/utils/imageModels';
import {
  adoptSelection,
  appendSubmission,
  applyJobToGroup,
  applyPreviewEventToGroup,
  createPreviewGroup,
  createSubmission,
  derivePreviewView,
  failSubmission,
  findSubmissionByJobId,
  normalizeUnitCount,
  replaceSubmission,
  selectSlotInGroup,
  submissionFromJob,
  type PreviewCompareSource,
  type PreviewGroup,
  type PreviewSlot,
  type PreviewSubmission,
  type PreviewView,
  type StreamingPreviewSlot
} from '$lib/stores/previewGroup';

export type { PreviewCompareSource, PreviewImage, PreviewSlot, PreviewSlotStatus, StreamingPreviewSlot } from '$lib/stores/previewGroup';

/** Derived view of the current preview group plus its identity. */
export type PreviewState = PreviewView & { groupId: string };

export type PromptFormState = {
  prompt: string;
  apiPath: ApiPath;
  size: string;
  model: string;
  quality: GenerateRequestBody['quality'];
  outputFormat: GenerateRequestBody['output_format'];
  complexity?: 'low' | 'medium' | 'high' | null;
  frameCount?: number;
  frameDurationMs?: number;
  background: NonNullable<GenerateRequestBody['background']>;
  outputCompression: string;
  quantity: number | string;
  responseFormat: ResponseFormatDefault;
  stream: boolean;
  partialImages: number;
  pasteBack: boolean;
  safetyIdentifier?: string;
};

export const DEFAULT_QUANTITY = 1;

/** Per-submission overrides that are not part of the editable form. */
export type SubmitOptions = {
  /** Pin a specific API preset instead of the active one (job retry). */
  apiPresetId?: string | null;
};

/** Tracker wiring supplied by the workspace; identity is the job id. */
export type PreviewCallbacks = {
  trackJob: (jobId: string) => void;
  /** Release every tracked job (clear, group replacement, cleanup). */
  releaseJobs?: () => void;
  loadJobs?: () => Promise<void>;
};

export const DEFAULT_PROMPT_MODEL = 'gpt-image-2';

export const initialPromptFormState: PromptFormState = {
  prompt: '',
  apiPath: '/v1/images/generations',
  size: 'auto',
  model: DEFAULT_PROMPT_MODEL,
  quality: 'auto',
  outputFormat: 'png',
  complexity: null,
  frameCount: 9,
  frameDurationMs: 200,
  background: 'auto',
  outputCompression: '',
  quantity: DEFAULT_QUANTITY,
  responseFormat: 'url',
  stream: false,
  partialImages: 2,
  pasteBack: true,
  safetyIdentifier: ''
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
    response_format: !isImage25(form.model) && !isImage26(form.model) && form.responseFormat ? (form.responseFormat as 'url' | 'b64_json') : null,
    api_path: form.apiPath
  };

  const special = form.outputFormat === 'svg' || form.outputFormat === 'gif';
  if (isImage26(form.model)) {
    delete body.response_format;
    body.api_path = '/v1/images/generations';
    if (form.complexity) body.complexity = form.complexity;
    if (form.outputFormat === 'gif') {
      body.frame_count = form.frameCount ?? 9;
      body.frame_duration_ms = form.frameDurationMs ?? 200;
    }
  }
  if (special) {
    body.output_compression = null;
    if (form.outputFormat === 'svg') body.n = 1;
    if (body.background?.startsWith('chroma_')) body.background = 'auto';
  }

  if (!special && form.outputFormat !== 'png' && form.outputCompression !== '') {
    body.output_compression = Math.min(Math.max(Number(form.outputCompression), 0), 100);
  }

  if (body.background === 'chroma_green' || body.background === 'chroma_magenta') {
    body.output_format = 'png';
    body.output_compression = null;
  } else if (body.background === 'transparent' && body.output_format === 'jpeg') {
    body.output_format = 'png';
    body.output_compression = null;
  }

  if (form.stream && !special) {
    body.stream = true;
    body.partial_images = Math.min(Math.max(Math.round(form.partialImages) || 2, 1), 3);
  }

  if (form.safetyIdentifier?.trim()) {
    body.safety_identifier = form.safetyIdentifier.trim();
  }

  return body;
}

function submissionError(body: GenerateRequestBody, edit = false, hasMask = false): string {
  const messages = get(t);
  const special = body.output_format === 'svg' || body.output_format === 'gif';
  if (special && !isImage26(body.model)) return messages.promptForm.mediaReset;
  // SVG is generation-only; GIF edits are fine until a mask is drawn.
  if (edit && (body.output_format === 'svg' || (body.output_format === 'gif' && hasMask))) return messages.promptForm.mediaReset;
  if (body.output_format === 'gif' && (!Number.isInteger(body.frame_count) || Number(body.frame_count) < 2 || Number(body.frame_count) > 64 || !Number.isInteger(body.frame_duration_ms) || Number(body.frame_duration_ms) < 20 || Number(body.frame_duration_ms) > 2000 || Number(body.frame_duration_ms) % 10 !== 0)) return messages.promptForm.invalidFrames;
  if (!body.prompt) return messages.messages.promptRequired;
  if (promptLength(body.prompt) > MAX_PROMPT_CHARS) return messages.promptForm.promptTooLong;
  if ((isImage25(body.model) || isImage26(body.model)) && !edit && body.api_path !== '/v1/images/generations') return messages.promptForm.image25Endpoint;
  if (!imageQualities(body.model).includes(body.quality)) return messages.promptForm.qualityReset;
  if ((edit || body.api_path === '/v1/images/generations') && !validOutputSize(body.size, body.output_format === 'gif')) return messages.sizeDialog.invalidSize;
  if (body.stream && !edit && !['/v1/images/generations', '/v1/responses'].includes(body.api_path || '')) {
    return messages.promptForm.streamUnsupportedPath;
  }
  if (!edit && body.background?.startsWith('chroma_') && body.api_path !== '/v1/images/generations') return messages.promptForm.chromaUnsupportedPath;
  return '';
}

function comparisonSourceForEdit(source: EditSourceState): PreviewCompareSource | null {
  if (!source.mask) return null;
  const primary = primaryEditSource(source);
  if (primary?.kind === 'gallery_primary') {
    if (!primary.previewUrl) return null;
    return { kind: 'gallery', url: primary.previewUrl, label: primary.previewLabel || primary.label };
  }
  // The source store may revoke its preview URL after submission. Keep the
  // File and let PreviewPanel own a separate, short-lived comparison URL.
  return primary?.kind === 'upload' ? { kind: 'upload', file: primary.file, label: primary.label } : null;
}

function queuedJob(response: GenerateJobResponse, body: GenerateRequestBody): GenerateJobStatus {
  const operation = response.operation || 'generation';
  return {
    job_id: response.job_id,
    status: response.status || 'queued',
    stage: response.stage || 'queued',
    message: response.message || (operation === 'edit' ? get(t).messages.queuedEdit : get(t).messages.queuedGeneration),
    operation,
    n: body.n ?? 1,
    prompt: body.prompt,
    model: body.model,
    size: body.size,
    quality: body.quality,
    output_format: body.output_format,
    background: body.background,
    streaming: Boolean(body.stream),
    partial_images: body.partial_images ?? null
  };
}

function createPreviewStore() {
  const { subscribe, set } = writable<PreviewState>({ ...derivePreviewView(createPreviewGroup(''), ''), groupId: '' });
  let group: PreviewGroup = createPreviewGroup('');
  let groupError = '';
  let groupEpoch = 0;
  let groupSeq = 0;
  let submissionSeq = 0;
  let lastRequest: GenerateRequestBody | null = null;
  let lastAction: 'generate' | 'edit' = 'generate';
  let lastPasteBack = true;
  let lastApiPresetId: string | null = null;

  function publish() {
    const view = derivePreviewView(group, groupError);
    // Record the first automatic pick (and drop a selection whose slot is
    // gone) so a later completion never re-points the main image.
    group = adoptSelection(group, view);
    // A failed job keeps its own message; submission-level errors take
    // precedence because they belong to the click the user just made.
    const error = groupError || jobFailureMessage(view.job, get(t).messages.jobFailed);
    set({ ...view, error, groupId: group.id });
  }

  function nextGroup() {
    groupSeq += 1;
    groupEpoch += 1;
    group = createPreviewGroup(`group-${groupSeq}`);
    groupError = '';
  }

  function nextSubmissionId() {
    submissionSeq += 1;
    return `submission-${submissionSeq}`;
  }

  function setError(message: string) {
    groupError = message;
    publish();
  }

  function clearPreview() {
    nextGroup();
    publish();
  }

  /** Replace the group with one job (history open, completion notification). */
  function openJob(job: GenerateJobStatus) {
    nextGroup();
    group = appendSubmission(group, submissionFromJob(nextSubmissionId(), job));
    publish();
  }

  function selectSlot(slotId: string) {
    const next = selectSlotInGroup(group, slotId);
    if (next === group) return;
    group = next;
    publish();
  }

  function applyJob(job: GenerateJobStatus) {
    const next = applyJobToGroup(group, job);
    if (next === group) return;
    group = next;
    publish();
  }

  function applyPreviewEvent(event: GeneratePreviewEvent) {
    const next = applyPreviewEventToGroup(group, event);
    if (next === group) return;
    group = next;
    publish();
  }

  /** A permanent tracking failure ends that submission only. */
  function failJobTracking(jobId: string, message: string) {
    const submission = findSubmissionByJobId(group, jobId);
    if (!submission) return;
    group = replaceSubmission(group, submission.id, (current) => ({
      ...current,
      error: message,
      job: current.job ? { ...current.job, status: 'error', error: message, message } : current.job
    }));
    publish();
  }

  function attachJob(submissionId: string, response: GenerateJobResponse, body: GenerateRequestBody) {
    group = replaceSubmission(group, submissionId, (submission) =>
      submission.id === submissionId
        ? { ...submission, jobId: response.job_id, job: queuedJob(response, body), submitStatus: 'accepted', error: '' }
        : submission
    );
    publish();
  }

  async function generateImage(
    form: PromptFormState,
    callbacks: PreviewCallbacks,
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

    // Generation appends to the current group, except when that group holds an
    // edit result: edits keep their own single-result preview (compare, mask,
    // paste-back), so the first generation after one starts a fresh group.
    if (group.submissions.some((entry) => entry.operation === 'edit')) {
      callbacks.releaseJobs?.();
      nextGroup();
    }
    // Reserve the slots synchronously, before the POST: repeated clicks each
    // get their own stable place even when the responses arrive out of order.
    const submission = createSubmission({
      id: nextSubmissionId(),
      operation: 'generation',
      prompt: body.prompt,
      expectedUnits: body.n ?? 1
    });
    group = appendSubmission(group, submission);
    groupError = '';
    publish();
    const epoch = groupEpoch;

    try {
      const response = await apiFetch<GenerateJobResponse>(
        '/api/generate',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'starting image generation'
      );
      // A late response still describes a real backend job, but it belongs to
      // a preview group the user already left: never attach it to the new one.
      if (epoch !== groupEpoch) return true;
      attachJob(submission.id, response, body);
      callbacks.trackJob(response.job_id);
      if (callbacks.loadJobs) await callbacks.loadJobs();
      return true;
    } catch (error) {
      if (epoch !== groupEpoch) return false;
      const message = error instanceof Error ? error.message : get(t).messages.requestFailed;
      group = failSubmission(group, submission.id, message);
      publish();
      return false;
    }
  }

  async function editImage(
    form: PromptFormState,
    editSource: EditSourceState,
    callbacks: PreviewCallbacks,
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
    // Markup can only sit on a gallery primary or an upload, never on a bare reference.
    const primary = primaryEditSource(editSource);
    const markupSourceId = primary && primary.kind !== 'gallery_reference' ? primary.id : undefined;
    if (editSource.markup && (editSource.mask || editSource.markup.sourceId !== markupSourceId)) {
      setError(get(t).promptForm.invalidMarkup);
      return false;
    }
    if (editSource.mask && !isMaskValid(editSource)) {
      setError(get(t).messages.editMaskStale);
      return false;
    }

    const body = buildRequestBody(form);
    if (body.background === 'chroma_green' || body.background === 'chroma_magenta') body.background = 'auto';
    const error = submissionError(body, true, Boolean(editSource.mask));
    if (error) {
      setError(error);
      return false;
    }
    if (options.apiPresetId) body.api_preset_id = options.apiPresetId;
    lastRequest = body;
    lastAction = 'edit';
    lastApiPresetId = options.apiPresetId || null;
    lastPasteBack = form.pasteBack;

    // Editing keeps its single-result interaction: it starts a fresh group.
    callbacks.releaseJobs?.();
    nextGroup();
    const submission = createSubmission({
      id: nextSubmissionId(),
      operation: 'edit',
      prompt: body.prompt,
      // The backend accepts n>1 for edits too, so reserve the requested units:
      // hardcoding 1 drops every extra streamed frame and ends the loading
      // state as soon as the first image lands.
      expectedUnits: body.n ?? 1,
      compareSource: comparisonSourceForEdit(editSource)
    });
    group = appendSubmission(group, submission);
    publish();
    const epoch = groupEpoch;

    const formData = new FormData();
    Object.entries(body).forEach(([key, value]) => {
      if (key === 'api_path') return;
      if (value !== null && value !== undefined && value !== '') {
        formData.append(key, String(value));
      }
    });

    const galleryPrimary = galleryPrimarySource(editSource);
    const endpoint = galleryPrimary
      ? `/api/edits/from-gallery/${encodeURIComponent(galleryPrimary.imageId)}`
      : '/api/edits';
    const uploadFieldName = sourceCount > 1 ? 'image[]' : 'image';
    uploadSources(editSource).forEach((source) => {
      formData.append(uploadFieldName, source.file, source.file.name);
    });
    galleryReferenceSources(editSource).forEach((ref) => {
      formData.append('reference_image_id', ref.imageId);
    });
    if (editSource.markup) formData.append('markup', editSource.markup.blob, 'markup.png');
    if (editSource.mask) {
      formData.append('mask', editSource.mask.blob, 'mask.png');
      formData.append('paste_back', String(form.pasteBack));
    }

    try {
      const response = await apiFetch<GenerateJobResponse>(
        endpoint,
        {
          method: 'POST',
          body: formData
        },
        'starting image edit'
      );
      if (epoch !== groupEpoch) return true;
      attachJob(submission.id, response, body);
      callbacks.trackJob(response.job_id);
      if (callbacks.loadJobs) await callbacks.loadJobs();
      return true;
    } catch (error) {
      if (epoch !== groupEpoch) return false;
      const message = error instanceof Error ? error.message : get(t).messages.requestFailed;
      group = failSubmission(group, submission.id, message);
      publish();
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
    nextGroup();
    publish();
  }

  return {
    subscribe,
    setError,
    clearPreview,
    openJob,
    selectSlot,
    applyJob,
    applyPreviewEvent,
    failJobTracking,
    generateImage,
    editImage,
    regenerate,
    cleanup
  };
}

export const previewStore = createPreviewStore();

/** Exposed for tests: the pure slot derivation lives in previewGroup.ts. */
export { derivePreviewView, normalizeUnitCount };
export type { PreviewGroup, PreviewSubmission, PreviewView };
