import type { GenerateJobStatusValue } from './common';

export type UsageSummary = {
  raw?: Record<string, unknown>;
  input_tokens?: number | null;
  output_tokens?: number | null;
  text_input_tokens?: number | null;
  image_input_tokens?: number | null;
  image_output_tokens?: number | null;
  total_tokens?: number | null;
  available?: boolean;
};

export type CostEstimate = {
  currency?: string;
  estimated_cost_usd?: number | null;
  rate_source?: string;
  pricing_model?: string | null;
  complete?: boolean;
  reason?: string | null;
};

export type GenerateJobResponse = {
  job_id: string;
  status: GenerateJobStatusValue;
  message?: string | null;
  stage?: string | null;
  operation?: 'generation' | 'edit' | null;
};

export type GenerateJobImage = {
  image_id: string;
  image_url: string;
  filename: string;
  unit_index?: number | null;
  result_index?: number | null;
  image_width?: number | null;
  image_height?: number | null;
  sent_prompt?: string | null;
  revised_prompt?: string | null;
  reported_size?: string | null;
  reported_quality?: string | null;
  upstream_duration_ms?: number | null;
  chroma_status?: string | null;
  diagnostics?: string[];
  paste_back?: string | null;
  paste_back_scale?: number | null;
};

export type GeneratePreviewEvent = {
  job_id: string;
  unit_index: number;
  call_index?: number;
  partial_image_index: number;
  sequence: number;
  mime_type: string;
  data_url: string;
};

export type GenerateJobStatus = GenerateJobResponse & {
  id?: string | null;
  image_id?: string | null;
  image_url?: string | null;
  images?: GenerateJobImage[];
  unit_statuses?: Record<string, string>;
  agent_turn_id?: string | null;
  agent_conversation_id?: string | null;
  prompt?: string | null;
  size?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  model?: string | null;
  quality?: string | null;
  output_format?: string | null;
  output_compression?: number | null;
  background?: string | null;
  response_format?: string | null;
  n?: number | null;
  completed_count?: number | null;
  success_count?: number | null;
  failure_count?: number | null;
  api_path?: string | null;
  api_preset_id?: string | null;
  api_preset_name?: string | null;
  duration?: string | null;
  stage_timings?: Record<string, number>;
  usage?: UsageSummary | null;
  cost?: CostEstimate | null;
  streaming?: boolean | null;
  partial_images?: number | null;
  mask_applied?: boolean | null;
  paste_back?: boolean | null;
  error?: string | null;
};

export type JobUnitRemoteSummary = {
  phase: string;
  task_id?: string | null;
  submitted_at?: string | null;
  deadline_at?: string | null;
  has_status_url: boolean;
  has_result_url: boolean;
  has_cancel_url: boolean;
  has_idempotency_key: boolean;
  poll_count?: number | null;
};

export type JobUnitDiagnostics = {
  unit_id: string;
  unit_index: number;
  status: string;
  stage?: string | null;
  message?: string | null;
  error?: string | null;
  attempts: number;
  recovery_count: number;
  remote?: JobUnitRemoteSummary | null;
  diagnostics?: Record<string, unknown> | null;
};

export type JobDiagnosticsResponse = {
  job_id: string;
  units: JobUnitDiagnostics[];
};
