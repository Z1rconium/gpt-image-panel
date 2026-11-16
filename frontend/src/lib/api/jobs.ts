import { apiFetch } from './client';
import { openJsonEventSource, type EventHandlers } from './events';
import type { GenerateRequestBody } from './types/generation';
import type {
  GenerateJobResponse,
  GenerateJobStatus,
  GeneratePreviewEvent,
  JobDiagnosticsResponse
} from './types/jobs';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const GENERATE_ENDPOINT = '/api/generate';
export const GENERATE_JOBS_ENDPOINT = '/api/generate/jobs';
export const GENERATE_JOBS_EVENTS_ENDPOINT = '/api/generate/jobs/events';
export const GENERATE_JOBS_HISTORY_ENDPOINT = '/api/generate/jobs/history';
export const EDITS_ENDPOINT = '/api/edits';

export function editFromGalleryUrl(imageId: string): string {
  return `/api/edits/from-gallery/${encodeURIComponent(imageId)}`;
}

export function generateJobUrl(jobId: string): string {
  return `${GENERATE_ENDPOINT}/${encodeURIComponent(jobId)}`;
}

export function generateJobEventsUrl(jobId: string): string {
  return `${generateJobUrl(jobId)}/events`;
}

export function generateJobDiagnosticsUrl(jobId: string): string {
  return `${generateJobUrl(jobId)}/diagnostics`;
}

export function generateJobMaskRestoreUrl(jobId: string, kind: 'mask' | 'markup' = 'mask'): string {
  return `${generateJobUrl(jobId)}/${kind}/restore`;
}

export function generateJobsHistoryUrl(params: URLSearchParams): string {
  return `${GENERATE_JOBS_ENDPOINT}?${params.toString()}`;
}

export function postGenerateJob(body: GenerateRequestBody) {
  return apiFetch<GenerateJobResponse>(
    GENERATE_ENDPOINT,
    { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) },
    'starting image generation'
  );
}

export function postEditJob(endpoint: string, formData: FormData) {
  return apiFetch<GenerateJobResponse>(
    endpoint,
    { method: 'POST', body: formData },
    'starting image edit'
  );
}

export function fetchActiveGenerateJobs() {
  return apiFetch<GenerateJobStatus[]>(GENERATE_JOBS_ENDPOINT, {}, 'loading jobs');
}

export function fetchGenerateJobHistory(params: URLSearchParams) {
  return apiFetch<GenerateJobStatus[]>(generateJobsHistoryUrl(params), {}, 'loading job history');
}

export function clearGenerateJobHistory() {
  return apiFetch(GENERATE_JOBS_HISTORY_ENDPOINT, { method: 'DELETE' }, 'clearing job history');
}

export function fetchGenerateJob(jobId: string, action = 'loading job') {
  return apiFetch<GenerateJobStatus>(generateJobUrl(jobId), {}, action);
}

export function cancelGenerateJob(jobId: string) {
  return apiFetch(generateJobUrl(jobId), { method: 'DELETE' }, 'cancelling job');
}

export function fetchJobDiagnostics(jobId: string) {
  return apiFetch<JobDiagnosticsResponse>(
    generateJobDiagnosticsUrl(jobId),
    {},
    'loading job diagnostics'
  );
}

export function openJobsEvents(handlers: EventHandlers<GenerateJobStatus[] | GenerateJobStatus>): EventSource {
  return openJsonEventSource<GenerateJobStatus[] | GenerateJobStatus>(
    GENERATE_JOBS_EVENTS_ENDPOINT,
    handlers,
    ['jobs', 'job']
  );
}

export function openGenerateJobEvents(
  jobId: string,
  handlers: EventHandlers<GenerateJobStatus | GeneratePreviewEvent>
): EventSource {
  return openJsonEventSource<GenerateJobStatus | GeneratePreviewEvent>(
    generateJobEventsUrl(jobId),
    handlers,
    ['job', 'preview']
  );
}
