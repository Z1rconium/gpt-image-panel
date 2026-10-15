import type { GenerateJobStatus } from '$lib/api/types/jobs';

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

function sameJobImage(
  left: NonNullable<GenerateJobStatus['images']>[number],
  right: NonNullable<GenerateJobStatus['images']>[number]
) {
  return (
    left.image_id === right.image_id &&
    left.image_url === right.image_url &&
    left.filename === right.filename &&
    left.image_width === right.image_width &&
    left.image_height === right.image_height
  );
}

function sameJobImages(left: GenerateJobStatus['images'], right: GenerateJobStatus['images']) {
  const leftImages = left || [];
  const rightImages = right || [];
  if (leftImages.length !== rightImages.length) return false;
  for (let index = 0; index < leftImages.length; index += 1) {
    const leftImage = leftImages[index];
    const rightImage = rightImages[index];
    if (!rightImage || !sameJobImage(leftImage, rightImage)) return false;
  }
  return true;
}

function sameStageTimings(left: GenerateJobStatus['stage_timings'], right: GenerateJobStatus['stage_timings']) {
  const leftTimings = left || {};
  const rightTimings = right || {};
  const leftKeys = Object.keys(leftTimings);
  const rightKeys = Object.keys(rightTimings);
  if (leftKeys.length !== rightKeys.length) return false;
  for (const key of leftKeys) {
    if (leftTimings[key] !== rightTimings[key]) return false;
  }
  return true;
}

function sameJob(left: GenerateJobStatus, right: GenerateJobStatus) {
  return (
    left.job_id === right.job_id &&
    left.status === right.status &&
    left.stage === right.stage &&
    left.message === right.message &&
    left.operation === right.operation &&
    left.updated_at === right.updated_at &&
    left.completed_at === right.completed_at &&
    left.image_id === right.image_id &&
    left.image_url === right.image_url &&
    left.prompt === right.prompt &&
    left.size === right.size &&
    left.model === right.model &&
    left.completed_count === right.completed_count &&
    left.success_count === right.success_count &&
    left.failure_count === right.failure_count &&
    left.duration === right.duration &&
    left.error === right.error &&
    sameJobImages(left.images, right.images) &&
    sameStageTimings(left.stage_timings, right.stage_timings)
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
