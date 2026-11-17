import type { GenerateJobImage, GenerateJobStatus } from '$lib/api/types/jobs';

export type JobsState = {
  jobs: GenerateJobStatus[];
  historyJobs: GenerateJobStatus[];
  historyLoading: boolean;
  historyLoaded: boolean;
  historyNeedsRefresh: boolean;
  historyHasMore: boolean;
  historyFailedOnly: boolean;
  selectedIds: Set<string>;
};

export const initialJobsState: JobsState = {
  jobs: [],
  historyJobs: [],
  historyLoading: false,
  historyLoaded: false,
  historyNeedsRefresh: false,
  historyHasMore: false,
  historyFailedOnly: false,
  selectedIds: new Set()
};

export const HISTORY_PAGE_SIZE = 50;
const HISTORY_CACHE_LIMIT = 500;

export function sameBaseJobImage(left: GenerateJobImage, right: GenerateJobImage): boolean {
  return (
    left.image_id === right.image_id &&
    left.image_url === right.image_url &&
    left.filename === right.filename
  );
}

export function sameJobImageList(
  left: GenerateJobImage[] | undefined,
  right: GenerateJobImage[] | undefined,
  sameExtra: (a: GenerateJobImage, b: GenerateJobImage) => boolean
): boolean {
  if (left === right) return true;
  const leftImages = left || [];
  const rightImages = right || [];
  if (leftImages.length !== rightImages.length) return false;
  for (let index = 0; index < leftImages.length; index += 1) {
    const leftImage = leftImages[index];
    const rightImage = rightImages[index];
    if (!rightImage || !sameBaseJobImage(leftImage, rightImage) || !sameExtra(leftImage, rightImage)) {
      return false;
    }
  }
  return true;
}

export function sameFlatRecord<T extends string | number>(
  left: Record<string, T> | undefined,
  right: Record<string, T> | undefined,
  emptyMatchesUndefined = true
): boolean {
  if (left === right) return true;
  if (!emptyMatchesUndefined && (!left || !right)) return !left && !right;
  const leftEntries = left || {};
  const rightEntries = right || {};
  const leftKeys = Object.keys(leftEntries);
  if (leftKeys.length !== Object.keys(rightEntries).length) return false;
  for (const key of leftKeys) {
    if (leftEntries[key] !== rightEntries[key]) return false;
  }
  return true;
}

/** Shared top-level job fields checked by both jobs-list diffing and preview snapshots. */
export function sameJobCore(left: GenerateJobStatus, right: GenerateJobStatus): boolean {
  if (left === right) return true;
  return (
    left.job_id === right.job_id &&
    left.status === right.status &&
    left.stage === right.stage &&
    left.message === right.message &&
    left.error === right.error &&
    left.completed_count === right.completed_count &&
    left.success_count === right.success_count &&
    left.failure_count === right.failure_count &&
    left.image_id === right.image_id &&
    left.image_url === right.image_url &&
    left.duration === right.duration &&
    left.completed_at === right.completed_at &&
    left.updated_at === right.updated_at
  );
}

/**
 * Jobs-list diffing = sameJobCore + operation/prompt/size/model + image dimensions + stage_timings.
 * Field policy matrix (with previewGroup.sameJobSnapshot): tests/unit/jobEqualityFields.test.ts.
 */
export function sameJob(left: GenerateJobStatus, right: GenerateJobStatus) {
  return (
    sameJobCore(left, right) &&
    left.operation === right.operation &&
    left.prompt === right.prompt &&
    left.size === right.size &&
    left.model === right.model &&
    sameJobImageList(
      left.images,
      right.images,
      (a, b) => a.image_width === b.image_width && a.image_height === b.image_height
    ) &&
    sameFlatRecord(left.stage_timings, right.stage_timings)
  );
}

export function sameJobList(left: GenerateJobStatus[], right: GenerateJobStatus[]) {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    const current = left[index];
    const next = right[index];
    if (!next || !sameJob(current, next)) return false;
  }
  return true;
}

export function sameStringSet(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}

export function mergeHistoryJobs(currentJobs: GenerateJobStatus[], nextJobs: GenerateJobStatus[]) {
  const merged: GenerateJobStatus[] = [];
  const seen = new Set<string>();
  for (const job of [...currentJobs, ...nextJobs]) {
    if (seen.has(job.job_id)) continue;
    seen.add(job.job_id);
    merged.push(job);
    if (merged.length >= HISTORY_CACHE_LIMIT) break;
  }
  return merged;
}
