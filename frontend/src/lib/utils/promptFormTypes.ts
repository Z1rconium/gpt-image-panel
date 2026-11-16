import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { GenerateRequestBody } from '$lib/api/types/generation';

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
