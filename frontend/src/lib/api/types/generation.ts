import type { ImageQuality } from '$lib/utils/imageModels';
import type { ApiPath } from './common';

export type GenerateRequestBody = {
  prompt: string;
  size: string;
  model: string;
  n: number;
  quality: ImageQuality;
  output_format: 'png' | 'jpeg' | 'webp' | 'svg' | 'gif';
  complexity?: 'low' | 'medium' | 'high' | null;
  frame_count?: number | null;
  frame_duration_ms?: number | null;
  output_compression?: number | null;
  background?: 'auto' | 'opaque' | 'transparent' | 'chroma_green' | 'chroma_magenta';
  response_format?: 'url' | 'b64_json' | null;
  api_path?: ApiPath | null;
  api_preset_id?: string | null;
  stream?: boolean;
  partial_images?: number;
  safety_identifier?: string | null;
};
