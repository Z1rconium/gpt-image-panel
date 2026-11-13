/**
 * Pure preview-group model: ordered submissions, stable slots and the derived
 * view the panel renders. No Svelte or network state lives here so the
 * ordering, de-duplication and error-isolation rules stay unit-testable.
 */
import type { GenerateJobImage, GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';
import { filenameFromImageUrl, isGifFilename, jobFailureMessage, thumbnailUrl } from '$lib/utils/format';
import { isActiveJobStatus } from '$lib/utils/jobs';

/** Latest streamed partial image for one task unit (keyed by unit_index). */
export type StreamingPreviewSlot = { dataUrl: string; sequence: number; unitIndex?: number; callIndex?: number };

export type PreviewCompareSource =
  | { kind: 'upload'; file: File; label: string }
  | { kind: 'gallery'; url: string; label: string };

export type PreviewImage = GenerateJobImage & { thumb_url: string };

export type PreviewSubmissionStatus = 'submitting' | 'accepted' | 'failed';

export type PreviewSubmission = {
  /** Stable client identity created before the POST; responses attach by it. */
  id: string;
  operation: 'generation' | 'edit';
  prompt: string;
  /** Normalized `n` of the request snapshot: the number of reserved slots. */
  expectedUnits: number;
  submitStatus: PreviewSubmissionStatus;
  jobId: string;
  job: GenerateJobStatus | null;
  error: string;
  streamingPreviews: Record<string, StreamingPreviewSlot>;
  compareSource: PreviewCompareSource | null;
};

export type PreviewGroup = {
  id: string;
  submissions: PreviewSubmission[];
  selectedSlotId: string;
};

export type PreviewSlotStatus = 'waiting' | 'streaming' | 'success' | 'failed' | 'cancelled';

export type PreviewSlot = {
  /** Stable across updates: submission + unit + result/call identity. */
  id: string;
  submissionId: string;
  jobId: string;
  unitIndex: number;
  /** Backend result index within its unit; null until a final image exists. */
  resultIndex: number | null;
  status: PreviewSlotStatus;
  image: PreviewImage | null;
  previewUrl: string;
  prompt: string;
  job: GenerateJobStatus | null;
  error: string;
};

export type PreviewView = {
  slots: PreviewSlot[];
  completedSlots: PreviewSlot[];
  pendingSlots: PreviewSlot[];
  selectedSlot: PreviewSlot | null;
  loading: boolean;
  error: string;
  job: GenerateJobStatus | null;
  prompt: string;
  imageUrl: string;
  filename: string;
  compareSource: PreviewCompareSource | null;
  hasFinalImage: boolean;
  completedCount: number;
  pendingCount: number;
  totalCount: number;
  /** Per-submission failures, shown next to the images they belong to. */
  submissionErrors: { id: string; message: string }[];
};

export const MAX_PREVIEW_UNITS = 10;
const MAX_PREVIEW_CALL_INDEX = 10;
const MAX_PREVIEW_FRAME_BYTES = 8 * 1024 * 1024;

export function createPreviewGroup(id: string): PreviewGroup {
  return { id, submissions: [], selectedSlotId: '' };
}

export function createSubmission(input: {
  id: string;
  operation: 'generation' | 'edit';
  prompt: string;
  expectedUnits: number;
  compareSource?: PreviewCompareSource | null;
}): PreviewSubmission {
  return {
    id: input.id,
    operation: input.operation,
    prompt: input.prompt,
    expectedUnits: normalizeUnitCount(input.expectedUnits),
    submitStatus: 'submitting',
    jobId: '',
    job: null,
    error: '',
    streamingPreviews: {},
    compareSource: input.compareSource ?? null
  };
}

export function submissionFromJob(id: string, job: GenerateJobStatus): PreviewSubmission {
  return {
    id,
    operation: job.operation === 'edit' ? 'edit' : 'generation',
    prompt: job.prompt || '',
    expectedUnits: normalizeUnitCount(job.n ?? 1),
    submitStatus: 'accepted',
    jobId: job.job_id,
    job,
    error: '',
    streamingPreviews: {},
    compareSource: null
  };
}

export function normalizeUnitCount(value: number | null | undefined): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(Math.max(Math.trunc(Number(value)) || 1, 1), MAX_PREVIEW_UNITS);
}

/** Normalize a job's results (and its single-image fallback) for display. */
export function normalizeJobImages(
  job: GenerateJobStatus | null,
  fallbackUrl = '',
  fallbackFilename = ''
): PreviewImage[] {
  const jobImages = job?.images?.filter((image) => image.image_url || image.filename) || [];
  if (jobImages.length) {
    return jobImages.map((image, index) => {
      const imageUrl = image.image_url || `/api/image/${encodeURIComponent(image.filename)}`;
      const imageFilename = image.filename || filenameFromImageUrl(imageUrl);
      return {
        image_id: image.image_id || imageFilename || `${job?.job_id || 'image'}-${index}`,
        image_url: imageUrl,
        filename: imageFilename,
        // These two identify the slot. Dropping them (or falling back to the
        // response array position) lets an image move to another unit's slot
        // whenever a different unit finishes first.
        unit_index: image.unit_index ?? null,
        result_index: image.result_index ?? null,
        thumb_url: imageFilename ? (isGifFilename(imageFilename) ? imageUrl : thumbnailUrl(imageFilename)) : imageUrl,
        image_width: image.image_width ?? null,
        image_height: image.image_height ?? null,
        sent_prompt: image.sent_prompt ?? null,
        revised_prompt: image.revised_prompt ?? null,
        reported_size: image.reported_size ?? null,
        reported_quality: image.reported_quality ?? null,
        upstream_duration_ms: image.upstream_duration_ms ?? null,
        chroma_status: image.chroma_status ?? null,
        diagnostics: image.diagnostics ?? [],
        paste_back: image.paste_back ?? null,
        paste_back_scale: image.paste_back_scale ?? null
      };
    });
  }
  if (!fallbackUrl) return [];
  const imageFilename = fallbackFilename || filenameFromImageUrl(fallbackUrl);
  return [
    {
      image_id: job?.image_id || fallbackFilename || fallbackUrl,
      image_url: fallbackUrl,
      filename: imageFilename,
      thumb_url: imageFilename ? (isGifFilename(imageFilename) ? fallbackUrl : thumbnailUrl(imageFilename)) : fallbackUrl,
      image_width: job?.image_width ?? null,
      image_height: job?.image_height ?? null
    }
  ];
}

function unitIndexOf(image: PreviewImage, fallbackIndex: number): number {
  const unit = image.unit_index;
  return Number.isInteger(unit) && Number(unit) >= 0 ? Number(unit) : fallbackIndex;
}

function resultIndexOf(image: PreviewImage, fallbackIndex: number): number {
  const result = image.result_index;
  return Number.isInteger(result) && Number(result) >= 0 ? Number(result) : fallbackIndex;
}

/** True when the job already delivered a final image for this unit. */
function jobHasUnitImage(job: GenerateJobStatus, unitIndex: number): boolean {
  return Boolean(job.images?.some((image, position) => unitIndexOf(image as PreviewImage, position) === unitIndex));
}

function streamingPreviewsForUnit(submission: PreviewSubmission, unitIndex: number) {
  return Object.entries(submission.streamingPreviews)
    .map(([key, preview]) => ({ key, preview, unitIndex: preview.unitIndex ?? Number(key.split(':')[0]) }))
    .filter((entry) => entry.unitIndex === unitIndex)
    .sort((left, right) => (left.preview.callIndex ?? 0) - (right.preview.callIndex ?? 0));
}

function pendingSlotStatus(submission: PreviewSubmission, unitIndex: number): PreviewSlotStatus {
  if (submission.submitStatus === 'failed') return 'failed';
  const job = submission.job;
  if (!job) return 'waiting';
  // A unit's own terminal state wins over the parent's running status: one
  // failed or cancelled unit must stop spinning immediately while its
  // siblings keep generating. Checking the parent first left a failed unit
  // "waiting" until the whole job finished.
  const unitStatus = job.unit_statuses?.[String(unitIndex)];
  if (unitStatus === 'cancelled') return 'cancelled';
  if (unitStatus && unitStatus !== 'success' && !isActiveJobStatus(unitStatus)) return 'failed';
  if (isActiveJobStatus(job.status)) return 'waiting';
  if (job.status === 'cancelled') return 'cancelled';
  // A terminal job must never leave a slot spinning, even when the backend
  // reports fewer units than the request reserved.
  return 'failed';
}

function slotsForSubmission(submission: PreviewSubmission): PreviewSlot[] {
  const job = submission.job;
  // Legacy single-image payloads carry only `image_url`; keep that fallback
  // reachable so such a job still shows its result instead of a stuck slot.
  const images = normalizeJobImages(job, job?.image_url || '', '');
  // The request snapshot reserves the slots. A job that reports more results
  // than that still shows them (see the loop below); a job that reports fewer
  // never invents extras.
  const expectedUnits = normalizeUnitCount(submission.expectedUnits || 1);
  const byUnit = new Map<number, PreviewImage[]>();
  images.forEach((image, position) => {
    // A job that omits unit_index (legacy single-image payloads) falls back to
    // its position; a reported unit_index always wins.
    const unitIndex = unitIndexOf(image, position);
    const bucket = byUnit.get(unitIndex) || [];
    bucket.push(image);
    byUnit.set(unitIndex, bucket);
  });
  // A legacy fallback image has no result identity of its own; it is the
  // unit's only result, so it takes result 0 rather than its array position.
  const legacyFallback = !job?.images?.length && images.length === 1;

  const slots: PreviewSlot[] = [];
  const pushUnit = (unitIndex: number) => {
    const results = byUnit.get(unitIndex) || [];
    if (results.length) {
      results.forEach((image, bucketIndex) => {
        // The slot id is the unit/result identity the backend reported, never
        // the position in the response array: units finish out of order, so
        // array position would move an image into another unit's slot.
        const resultIndex = legacyFallback ? 0 : resultIndexOf(image, bucketIndex);
        slots.push({
          id: `${submission.id}:u${unitIndex}:r${resultIndex}`,
          submissionId: submission.id,
          jobId: submission.jobId,
          unitIndex,
          resultIndex,
          status: 'success',
          image,
          previewUrl: image.image_url,
          prompt: submission.prompt,
          job,
          error: ''
        });
      });
      return;
    }
    const streams = job && isActiveJobStatus(job.status) ? streamingPreviewsForUnit(submission, unitIndex) : [];
    if (streams.length) {
      streams.forEach(({ key, preview }) => {
        slots.push({
          id: `${submission.id}:u${unitIndex}:c${preview.callIndex ?? 0}:${key}`,
          submissionId: submission.id,
          jobId: submission.jobId,
          unitIndex,
          resultIndex: null,
          status: 'streaming',
          image: null,
          previewUrl: preview.dataUrl,
          prompt: submission.prompt,
          job,
          error: ''
        });
      });
      return;
    }
    slots.push({
      id: `${submission.id}:u${unitIndex}:p`,
      submissionId: submission.id,
      jobId: submission.jobId,
      unitIndex,
      resultIndex: null,
      status: pendingSlotStatus(submission, unitIndex),
      image: null,
      previewUrl: '',
      prompt: submission.prompt,
      job,
      error: submission.error
    });
  };

  for (let unitIndex = 0; unitIndex < expectedUnits; unitIndex += 1) pushUnit(unitIndex);
  // Results the backend produced beyond the reserved count stay reachable.
  [...byUnit.keys()]
    .filter((unitIndex) => unitIndex >= expectedUnits)
    .sort((left, right) => left - right)
    .forEach(pushUnit);
  return slots;
}

/** Submission order, then unit order, then result order (never response order). */
export function derivePreviewSlots(group: PreviewGroup): PreviewSlot[] {
  return group.submissions.flatMap(slotsForSubmission);
}

/**
 * Persist the first automatic selection. Without this, the "first completed
 * slot" fallback would re-point the main image at whichever submission
 * happens to finish first, so a later completion steals the view.
 *
 * An existing selection is never overwritten here: if its slot is missing
 * from one snapshot the view falls back, and the user's pick returns with it.
 */
export function adoptSelection(group: PreviewGroup, view: PreviewView): PreviewGroup {
  if (group.selectedSlotId) return group;
  const selectedId = view.selectedSlot?.id || '';
  return selectedId ? { ...group, selectedSlotId: selectedId } : group;
}

export function derivePreviewView(group: PreviewGroup, error = '', jobFallbackError = ''): PreviewView {
  const slots = derivePreviewSlots(group);
  const completedSlots = slots.filter((slot) => slot.status === 'success' && slot.image);
  const pendingSlots = slots.filter((slot) => slot.status === 'waiting' || slot.status === 'streaming');
  const selectedSlot = completedSlots.find((slot) => slot.id === group.selectedSlotId) || completedSlots[0] || null;
  const latestSubmission = group.submissions[group.submissions.length - 1] || null;
  const selectedSubmission = selectedSlot
    ? group.submissions.find((submission) => submission.id === selectedSlot.submissionId) || null
    : null;
  const job = selectedSlot?.job || latestSubmission?.job || null;
  const loading = pendingSlots.length > 0 || group.submissions.some((submission) => submission.submitStatus === 'submitting');
  // Include backend job failures on sibling/unselected submissions so a running
  // or completed earlier click never silently hides a later job failure.
  const submissionErrors = group.submissions.flatMap((submission) => {
    if (submission.error) return [{ id: submission.id, message: submission.error }];
    if (submission.job && submission.job.job_id !== job?.job_id) {
      const failure = jobFailureMessage(submission.job, jobFallbackError);
      if (failure) return [{ id: submission.id, message: failure }];
    }
    return [];
  });
  return {
    slots,
    completedSlots,
    pendingSlots,
    selectedSlot,
    loading,
    error,
    job,
    prompt: selectedSlot?.prompt || latestSubmission?.prompt || '',
    imageUrl: selectedSlot?.image?.image_url || '',
    filename: selectedSlot?.image?.filename || '',
    compareSource: selectedSubmission?.compareSource || latestSubmission?.compareSource || null,
    hasFinalImage: completedSlots.length > 0,
    completedCount: completedSlots.length,
    pendingCount: pendingSlots.length,
    totalCount: slots.length,
    submissionErrors
  };
}

export function appendSubmission(group: PreviewGroup, submission: PreviewSubmission): PreviewGroup {
  return { ...group, submissions: [...group.submissions, submission] };
}

export function replaceSubmission(
  group: PreviewGroup,
  submissionId: string,
  patch: (submission: PreviewSubmission) => PreviewSubmission
): PreviewGroup {
  let changed = false;
  const submissions = group.submissions.map((submission) => {
    if (submission.id !== submissionId) return submission;
    const next = patch(submission);
    if (next !== submission) changed = true;
    return next;
  });
  return changed ? { ...group, submissions } : group;
}

export function findSubmissionByJobId(group: PreviewGroup, jobId: string): PreviewSubmission | null {
  if (!jobId) return null;
  return group.submissions.find((submission) => submission.jobId === jobId) || null;
}

export function attachSubmissionJob(
  group: PreviewGroup,
  submissionId: string,
  jobId: string,
  job: GenerateJobStatus
): PreviewGroup {
  return replaceSubmission(group, submissionId, (submission) =>
    submission.id === submissionId
      ? { ...submission, jobId, job, submitStatus: 'accepted', error: '' }
      : submission
  );
}

export function failSubmission(group: PreviewGroup, submissionId: string, message: string): PreviewGroup {
  return replaceSubmission(group, submissionId, (submission) =>
    submission.id === submissionId
      ? { ...submission, submitStatus: 'failed', error: message, streamingPreviews: {} }
      : submission
  );
}

function streamingStillRelevant(job: GenerateJobStatus, slot: StreamingPreviewSlot, key: string) {
  const unitIndex = slot.unitIndex ?? Number(key.split(':')[0]);
  if (jobHasUnitImage(job, unitIndex)) return false;
  const unitStatus = job.unit_statuses?.[String(unitIndex)];
  return !unitStatus || isActiveJobStatus(unitStatus);
}

/** Apply a job snapshot to its owning submission; other submissions are untouched. */
export function applyJobToGroup(group: PreviewGroup, job: GenerateJobStatus): PreviewGroup {
  const submission = findSubmissionByJobId(group, job.job_id);
  if (!submission) return group;
  const active = isActiveJobStatus(job.status);
  const streamingPreviews = active
    ? Object.fromEntries(
        Object.entries(submission.streamingPreviews).filter(([key, slot]) => streamingStillRelevant(job, slot, key))
      )
    : {};
  const sameStreaming =
    Object.keys(streamingPreviews).length === Object.keys(submission.streamingPreviews).length &&
    Object.entries(streamingPreviews).every(([key, slot]) => submission.streamingPreviews[key] === slot);
  if (
    submission.job === job &&
    submission.submitStatus === 'accepted' &&
    sameStreaming
  ) {
    return group;
  }
  return replaceSubmission(group, submission.id, (current) => ({
    ...current,
    job,
    submitStatus: 'accepted',
    streamingPreviews
  }));
}

export function applyPreviewEventToGroup(group: PreviewGroup, event: GeneratePreviewEvent): PreviewGroup {
  const submission = findSubmissionByJobId(group, event.job_id);
  if (!submission?.job) return group;
  const job = submission.job;
  // The final image always wins, and out-of-order/duplicate frames (possible
  // across an SSE reconnect) are dropped via the per-unit sequence so one
  // image slot never shows another unit's frame.
  if (!isActiveJobStatus(job.status)) return group;
  const expectedUnits = normalizeUnitCount(submission.expectedUnits || 1);
  if (!Number.isInteger(event.unit_index) || event.unit_index < 0 || event.unit_index >= expectedUnits) return group;
  const callIndex = event.call_index || 0;
  if (!Number.isInteger(callIndex) || callIndex < 0 || callIndex >= MAX_PREVIEW_CALL_INDEX) return group;
  if (typeof event.data_url !== 'string' || event.data_url.length > MAX_PREVIEW_FRAME_BYTES) return group;
  if (jobHasUnitImage(job, event.unit_index)) return group;
  const unitStatus = job.unit_statuses?.[String(event.unit_index)];
  if (unitStatus && !isActiveJobStatus(unitStatus)) return group;
  const key = callIndex ? `${event.unit_index}:${callIndex}` : `${event.unit_index}`;
  const existing = submission.streamingPreviews[key];
  if (existing && event.sequence <= existing.sequence) return group;
  return replaceSubmission(group, submission.id, (current) => ({
    ...current,
    streamingPreviews: {
      ...current.streamingPreviews,
      [key]: { dataUrl: event.data_url, sequence: event.sequence, unitIndex: event.unit_index, callIndex }
    }
  }));
}

export function selectSlotInGroup(group: PreviewGroup, slotId: string): PreviewGroup {
  if (group.selectedSlotId === slotId) return group;
  return { ...group, selectedSlotId: slotId };
}
