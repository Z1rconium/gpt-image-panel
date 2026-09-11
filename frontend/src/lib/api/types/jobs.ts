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
  image_width?: number | null;
  image_height?: number | null;
};

export type GenerateJobStatus = GenerateJobResponse & {
  id?: string | null;
  image_id?: string | null;
  image_url?: string | null;
  images?: GenerateJobImage[];
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
  api_preset_name?: string | null;
  duration?: string | null;
  stage_timings?: Record<string, number>;
  usage?: UsageSummary | null;
  cost?: CostEstimate | null;
  error?: string | null;
};

