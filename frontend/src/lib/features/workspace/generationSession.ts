import { fetchGenerateJob, postEditJob, postGenerateJob } from '$lib/api/jobs';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { GenerateJobResponse, GenerateJobStatus, GeneratePreviewEvent } from '$lib/api/types/jobs';
import type { EditSourceState } from '$lib/stores/editSource';
import type { PreviewCallbacks, SubmitOptions } from '$lib/stores/preview';
import { isActiveJobStatus } from '$lib/utils/jobs';
import type { PromptFormState } from '$lib/utils/promptFormTypes';

const MAX_HANDLED_TERMINAL_JOBS = 200;
const GALLERY_REFRESH_DEBOUNCE_MS = 350;

export type GenerationTransport = {
  postGenerate: (body: GenerateRequestBody) => Promise<GenerateJobResponse>;
  postEdit: (endpoint: string, formData: FormData) => Promise<GenerateJobResponse>;
  getJob: (jobId: string) => Promise<GenerateJobStatus>;
};

export const defaultGenerationTransport: GenerationTransport = {
  postGenerate: postGenerateJob,
  postEdit: postEditJob,
  getJob: (jobId) => fetchGenerateJob(jobId, 'opening completed task')
};

export type GenerationSessionPreview = {
  openJob: (job: GenerateJobStatus) => void;
  applyJob: (job: GenerateJobStatus) => void;
  applyPreviewEvent: (event: GeneratePreviewEvent) => void;
  failJobTracking: (jobId: string, message: string) => void;
  generateImage: (
    form: PromptFormState,
    callbacks: PreviewCallbacks,
    options?: SubmitOptions
  ) => Promise<boolean>;
  editImage: (
    form: PromptFormState,
    editSource: EditSourceState,
    callbacks: PreviewCallbacks,
    options?: SubmitOptions
  ) => Promise<boolean>;
  clearPreview: () => void;
};

export type GenerationSessionJobs = {
  trackJob: (
    jobId: string,
    updatePreviewFromJob: (job: GenerateJobStatus) => void | Promise<void>,
    setPreviewError: (message: string) => void,
    onPreview?: (event: GeneratePreviewEvent) => void,
    options?: { streaming?: boolean; status?: string }
  ) => void;
  untrackJob: (jobId: string) => void;
  closeActiveJobSource: () => void;
  loadJobs: () => Promise<void>;
  markHistoryStale: () => void;
  shouldRefreshJobsAfterSubmit: () => boolean;
};

export type CreateGenerationSessionOptions = {
  preview: GenerationSessionPreview;
  jobs: GenerationSessionJobs;
  onGalleryRefresh: () => void | Promise<void>;
  onShowNewGalleryToast: () => void;
  isGalleryFirstPage: () => boolean;
  transport?: Partial<GenerationTransport>;
  galleryRefreshDebounceMs?: number;
};

/**
 * Coordinates workspace generation/edit submission, job tracking, terminal
 * side-effects (history invalidation and debounced gallery refresh), and job
 * reopening outside Svelte components.
 */
export function createGenerationSession({
  preview,
  jobs,
  onGalleryRefresh,
  onShowNewGalleryToast,
  isGalleryFirstPage,
  transport,
  galleryRefreshDebounceMs = GALLERY_REFRESH_DEBOUNCE_MS
}: CreateGenerationSessionOptions) {
  const resolvedTransport: GenerationTransport = {
    ...defaultGenerationTransport,
    ...transport
  };
  const handledTerminalJobIds = new Set<string>();
  let gallerySuccessRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;
  let openJobSeq = 0;

  function scheduleGalleryRefresh() {
    if (disposed) return;
    if (gallerySuccessRefreshTimer) clearTimeout(gallerySuccessRefreshTimer);
    gallerySuccessRefreshTimer = setTimeout(() => {
      gallerySuccessRefreshTimer = null;
      if (disposed) return;
      if (!isGalleryFirstPage()) {
        onShowNewGalleryToast();
        return;
      }
      void onGalleryRefresh();
    }, galleryRefreshDebounceMs);
  }

  function recordTerminalJob(jobId: string): boolean {
    if (handledTerminalJobIds.has(jobId)) return false;
    handledTerminalJobIds.add(jobId);
    if (handledTerminalJobIds.size > MAX_HANDLED_TERMINAL_JOBS) {
      const oldestJobId = handledTerminalJobIds.values().next().value;
      if (oldestJobId) handledTerminalJobIds.delete(oldestJobId);
    }
    return true;
  }

  function updatePreviewFromJob(job: GenerateJobStatus) {
    if (disposed) return;
    // Only the owning submission changes; every other job keeps its slots.
    preview.applyJob(job);
    if (!isActiveJobStatus(job.status)) {
      if (!recordTerminalJob(job.job_id)) return;
      if (jobs.shouldRefreshJobsAfterSubmit()) void jobs.loadJobs();
      jobs.markHistoryStale();
      if (job.status === 'success' || job.status === 'partial_failure') {
        scheduleGalleryRefresh();
      }
    }
  }

  function trackJob(jobId: string, meta?: { streaming?: boolean; status?: string }) {
    if (disposed) return;
    jobs.trackJob(
      jobId,
      (job) => updatePreviewFromJob(job),
      (message) => preview.failJobTracking(jobId, message),
      preview.applyPreviewEvent,
      meta
    );
  }

  const previewCallbacks: PreviewCallbacks = {
    trackJob,
    releaseJobs: () => jobs.closeActiveJobSource(),
    releaseJob: (jobId: string) => jobs.untrackJob(jobId),
    get loadJobs() {
      return jobs.shouldRefreshJobsAfterSubmit() ? jobs.loadJobs : undefined;
    },
    postGenerate: (body) => resolvedTransport.postGenerate(body),
    postEdit: (endpoint, formData) => resolvedTransport.postEdit(endpoint, formData)
  };

  function submitGenerate(form: PromptFormState, options?: SubmitOptions): Promise<boolean> {
    if (disposed) return Promise.resolve(false);
    return preview.generateImage(form, previewCallbacks, options);
  }

  function submitEdit(
    form: PromptFormState,
    editSource: EditSourceState,
    options?: SubmitOptions
  ): Promise<boolean> {
    if (disposed) return Promise.resolve(false);
    return preview.editImage(form, editSource, previewCallbacks, options);
  }

  function resumeJob(job: GenerateJobStatus) {
    if (disposed) return;
    preview.openJob(job);
    if (isActiveJobStatus(job.status)) {
      trackJob(job.job_id, { streaming: Boolean(job.streaming), status: job.status });
    }
  }

  async function openJob(
    jobId: string,
    onBeforeOpen?: (job: GenerateJobStatus) => void
  ): Promise<GenerateJobStatus | null> {
    const seq = ++openJobSeq;
    const job = await resolvedTransport.getJob(jobId);
    if (disposed || seq !== openJobSeq) return null;
    jobs.closeActiveJobSource();
    onBeforeOpen?.(job);
    preview.openJob(job);
    if (isActiveJobStatus(job.status)) {
      trackJob(job.job_id, { streaming: Boolean(job.streaming), status: job.status });
    }
    return job;
  }

  function clearPreview() {
    // Clearing ends display tracking only; the backend jobs keep running.
    jobs.closeActiveJobSource();
    preview.clearPreview();
  }

  function dispose() {
    disposed = true;
    openJobSeq += 1;
    if (gallerySuccessRefreshTimer) clearTimeout(gallerySuccessRefreshTimer);
    gallerySuccessRefreshTimer = null;
    handledTerminalJobIds.clear();
  }

  return {
    submitGenerate,
    submitEdit,
    resumeJob,
    openJob,
    clearPreview,
    scheduleGalleryRefresh,
    dispose
  };
}

export type GenerationSession = ReturnType<typeof createGenerationSession>;
