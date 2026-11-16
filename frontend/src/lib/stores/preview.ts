import { get, writable } from 'svelte/store';
import { EDITS_ENDPOINT, editFromGalleryUrl, postEditJob, postGenerateJob } from '$lib/api/jobs';
import { t } from '$lib/i18n';
import {
  MAX_EDIT_SOURCE_IMAGES,
  editSourceCount,
  editSourceStore,
  galleryPrimarySource,
  galleryReferenceSources,
  primaryEditSource,
  submitBlocker,
  uploadSources,
  type EditSourceState,
  type EditSourceSubmitBlocker
} from '$lib/stores/editSource';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { GeneratePreviewEvent, GenerateJobResponse, GenerateJobStatus } from '$lib/api/types/jobs';
import { imageUrl, jobFailureMessage } from '$lib/utils/format';
import { initialPromptFormState, type PromptFormState } from '$lib/utils/promptFormTypes';
import { buildRequestBody, validateSubmission, type SubmissionErrorKey } from '$lib/utils/promptRules';
import {
  MAX_CONCURRENT_SUBMISSIONS,
  adoptSelection,
  appendSubmission,
  applyJobToGroup,
  applyPreviewEventToGroup,
  attachSubmissionJob,
  countActiveSubmissions,
  createPreviewGroup,
  createSubmission,
  derivePreviewView,
  failSubmission,
  findSubmissionByJobId,
  normalizeUnitCount,
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

/** Per-submission overrides that are not part of the editable form. */
export type SubmitOptions = {
  /** Pin a specific API preset instead of the active one (job retry). */
  apiPresetId?: string | null;
};

/** Trackers released when an edit starts its own group. */
export type PreviewCallbacks = {
  trackJob: (jobId: string, meta?: { streaming?: boolean; status?: string }) => void;
  /** Release every tracked job (clear, cleanup, explicit group replacement). */
  releaseJobs?: () => void;
  /**
   * Release one tracked job. An edit starts its own group but must not stop a
   * generation that is still running, so it releases only the jobs it replaces.
   */
  releaseJob?: (jobId: string) => void;
  loadJobs?: () => Promise<void>;
  postGenerate?: (body: GenerateRequestBody) => Promise<GenerateJobResponse>;
  postEdit?: (endpoint: string, formData: FormData) => Promise<GenerateJobResponse>;
};

function submissionErrorMessage(key: SubmissionErrorKey): string {
  const messages = get(t);
  switch (key) {
    case 'media_reset':
      return messages.promptForm.mediaReset;
    case 'invalid_frames':
      return messages.promptForm.invalidFrames;
    case 'prompt_required':
      return messages.messages.promptRequired;
    case 'prompt_too_long':
      return messages.promptForm.promptTooLong;
    case 'image25_endpoint':
      return messages.promptForm.image25Endpoint;
    case 'quality_reset':
      return messages.promptForm.qualityReset;
    case 'invalid_size':
      return messages.sizeDialog.invalidSize;
    case 'stream_unsupported_path':
      return messages.promptForm.streamUnsupportedPath;
    case 'chroma_unsupported_path':
      return messages.promptForm.chromaUnsupportedPath;
  }
}

function submissionError(body: GenerateRequestBody, edit = false, hasMask = false): string {
  const key = validateSubmission(body, { edit, hasMask });
  return key ? submissionErrorMessage(key) : '';
}

function comparisonSourceForEdit(source: EditSourceState): PreviewCompareSource | null {
  if (!source.mask) return null;
  const primary = primaryEditSource(source);
  if (primary?.kind === 'gallery_primary' || primary?.kind === 'gallery_reference') {
    const url = primary.filename ? imageUrl(primary.filename) : primary.previewUrl;
    if (!url) return null;
    return { kind: 'gallery', url, label: primary.previewLabel || primary.label };
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
    const fallbackJobError = get(t).messages.jobFailed;
    const view = derivePreviewView(group, groupError, fallbackJobError);
    // Record the first automatic pick (and drop a selection whose slot is
    // gone) so a later completion never re-points the main image.
    group = adoptSelection(group, view);
    // A failed job keeps its own message; submission-level errors take
    // precedence because they belong to the click the user just made.
    const error = groupError || jobFailureMessage(view.job, fallbackJobError);
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
    // Only the submission's own error is set: its slots report the failure
    // through `submitStatus`, so the job's real status is never overwritten.
    group = failSubmission(group, submission.id, message);
    publish();
  }

  function attachJob(submissionId: string, response: GenerateJobResponse, body: GenerateRequestBody) {
    group = attachSubmissionJob(group, submissionId, response.job_id, queuedJob(response, body));
    publish();
  }

  async function submitAndTrack(
    submissionId: string,
    epoch: number,
    body: GenerateRequestBody,
    callbacks: PreviewCallbacks,
    send: () => Promise<GenerateJobResponse>
  ): Promise<boolean> {
    try {
      const response = await send();
      // A late response still describes a real backend job, but it belongs to
      // a preview group the user already left: never attach it to the new one.
      if (epoch !== groupEpoch) return true;
      attachJob(submissionId, response, body);
      callbacks.trackJob(response.job_id, {
        streaming: Boolean(body.stream),
        status: response.status || 'queued'
      });
      if (callbacks.loadJobs) await callbacks.loadJobs();
      return true;
    } catch (error) {
      if (epoch !== groupEpoch) return false;
      const message = error instanceof Error ? error.message : get(t).messages.requestFailed;
      group = failSubmission(group, submissionId, message);
      publish();
      return false;
    }
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
    if (countActiveSubmissions(group) >= MAX_CONCURRENT_SUBMISSIONS) {
      setError(get(t).messages.tooManyActiveSubmissions(MAX_CONCURRENT_SUBMISSIONS));
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
    const send = callbacks.postGenerate ?? postGenerateJob;
    return submitAndTrack(submission.id, epoch, body, callbacks, () => send(body));
  }

  function editSourceBlockerMessage(blocker: EditSourceSubmitBlocker): string {
    const catalog = get(t);
    switch (blocker) {
      case 'no_sources':
        return catalog.messages.editSourceRequired;
      case 'too_many_sources':
        return catalog.messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES);
      case 'invalid_markup':
        return catalog.promptForm.invalidMarkup;
      case 'stale_mask':
        return catalog.messages.editMaskStale;
    }
  }

  async function editImage(
    form: PromptFormState,
    editSource: EditSourceState,
    callbacks: PreviewCallbacks,
    options: SubmitOptions = {}
  ): Promise<boolean> {
    const blocker = submitBlocker(editSource);
    if (blocker) {
      setError(editSourceBlockerMessage(blocker));
      return false;
    }
    const sourceCount = editSourceCount(editSource);

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
    // Only the jobs this group replaces are released; a generation submitted
    // moments earlier keeps streaming (and stays in the running list).
    if (callbacks.releaseJob) {
      group.submissions.forEach((entry) => {
        if (entry.jobId) callbacks.releaseJob?.(entry.jobId);
      });
    } else {
      callbacks.releaseJobs?.();
    }
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
    const endpoint = galleryPrimary ? editFromGalleryUrl(galleryPrimary.imageId) : EDITS_ENDPOINT;
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

    const send = callbacks.postEdit ?? postEditJob;
    return submitAndTrack(submission.id, epoch, body, callbacks, () => send(endpoint, formData));
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
      complexity: lastRequest.complexity ?? null,
      frameCount: lastRequest.frame_count ?? initialPromptFormState.frameCount,
      frameDurationMs: lastRequest.frame_duration_ms ?? initialPromptFormState.frameDurationMs,
      background: (lastRequest.background as PromptFormState['background']) ?? 'auto',
      outputCompression: lastRequest.output_compression === null || lastRequest.output_compression === undefined ? '' : String(lastRequest.output_compression),
      responseFormat: lastRequest.response_format || '',
      stream: Boolean(lastRequest.stream),
      partialImages: lastRequest.partial_images ?? initialPromptFormState.partialImages,
      pasteBack: lastPasteBack,
      safetyIdentifier: lastRequest.safety_identifier ?? ''
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
