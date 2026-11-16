import type { ApiPath } from '$lib/api/types/common';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { ProviderCapabilities } from '$lib/api/types/settings';
import {
  MAX_PROMPT_CHARS,
  imageQualities,
  isImage25,
  isImage26,
  promptLength,
  validOutputSize
} from '$lib/utils/imageModels';
import { DEFAULT_QUANTITY, type PromptFormState } from '$lib/utils/promptFormTypes';

export type SubmissionErrorKey =
  | 'media_reset'
  | 'invalid_frames'
  | 'prompt_required'
  | 'prompt_too_long'
  | 'image25_endpoint'
  | 'quality_reset'
  | 'invalid_size'
  | 'stream_unsupported_path'
  | 'chroma_unsupported_path';

export type ValidateSubmissionContext = {
  edit?: boolean;
  hasMask?: boolean;
};

export type ResetReason =
  | 'complexity_reset'
  | 'media_reset'
  | 'quality_reset'
  | 'stream_reset'
  | 'transparent_unsupported'
  | 'chroma_reset'
  | 'chroma_to_png'
  | 'transparent_jpeg_to_png'
  | 'compression_reset';

export type NormalizeFormContext = {
  providerMapped?: boolean;
  providerCaps?: ProviderCapabilities | null;
  editMode: boolean;
  hasMask: boolean;
};

export type NormalizeFormResult = {
  form: PromptFormState;
  resets: ResetReason[];
};

const STREAM_SUPPORTED_PATHS: readonly ApiPath[] = ['/v1/images/generations', '/v1/responses'];

export function isPromptOnlyApiPath(apiPath: ApiPath): boolean {
  return apiPath === '/v1/responses' || apiPath === '/v1/chat/completions';
}

export function isStreamUnsupportedPath(apiPath: ApiPath, editMode: boolean): boolean {
  return !editMode && !STREAM_SUPPORTED_PATHS.includes(apiPath);
}

export function isStreamUnavailableMapped(
  providerMapped: boolean,
  providerCaps?: ProviderCapabilities | null
): boolean {
  return providerMapped && !providerCaps?.stream;
}

export function isTransparentUnavailableMapped(
  providerMapped: boolean,
  providerCaps?: ProviderCapabilities | null
): boolean {
  return providerMapped && Boolean(providerCaps) && !providerCaps!.transparent_background;
}

/**
 * Normalizes incompatible prompt-form controls in a fixed, idempotent order.
 * Rule table and ordering rationale live in the C6 prompt-rules research doc.
 */
export function normalizeForm(form: PromptFormState, ctx: NormalizeFormContext): NormalizeFormResult {
  const next: PromptFormState = { ...form };
  const resets: ResetReason[] = [];
  const pushReset = (reason: ResetReason) => {
    if (!resets.includes(reason)) resets.push(reason);
  };

  const providerMapped = Boolean(ctx.providerMapped);
  const providerCaps = ctx.providerCaps ?? null;

  // N1: Provider complexity gate
  if (providerMapped && !providerCaps?.complexity && next.complexity !== null && next.complexity !== undefined) {
    next.complexity = null;
    pushReset('complexity_reset');
  }

  // N2: Special media (svg/gif) eligibility gate (model, edit/mask, mapped provider)
  if (next.outputFormat === 'svg' || next.outputFormat === 'gif') {
    const unsupportedForEdit = next.outputFormat === 'svg' ? ctx.editMode : ctx.hasMask;
    const unsupportedByProvider =
      providerMapped &&
      (!providerCaps?.formats?.includes(next.outputFormat) ||
        (next.outputFormat === 'gif' && !providerCaps?.animation));
    if (!isImage26(next.model) || unsupportedForEdit || unsupportedByProvider) {
      next.outputFormat = 'png';
      pushReset('media_reset');
    }
  }

  // N3: Special media (svg/gif) side-effects (only when svg/gif survived N2)
  if (next.outputFormat === 'svg' || next.outputFormat === 'gif') {
    let mediaSideEffectReset = false;
    if (next.stream) {
      next.stream = false;
      mediaSideEffectReset = true;
    }
    if (next.outputCompression !== '') {
      next.outputCompression = '';
      mediaSideEffectReset = true;
    }
    if (next.background?.startsWith('chroma_')) {
      next.background = 'auto';
      mediaSideEffectReset = true;
    }
    if (next.outputFormat === 'svg' && Number(next.quantity) !== 1) {
      next.quantity = 1;
      mediaSideEffectReset = true;
    }
    if (mediaSideEffectReset) {
      pushReset('media_reset');
    }
  }

  // N4: Quality compatibility
  if (!imageQualities(next.model).includes(next.quality)) {
    next.quality = 'auto';
    pushReset('quality_reset');
  }

  // N5: Stream compatibility
  if (
    next.stream &&
    (isStreamUnsupportedPath(next.apiPath, ctx.editMode) ||
      isStreamUnavailableMapped(providerMapped, providerCaps))
  ) {
    next.stream = false;
    pushReset('stream_reset');
  }

  // N6: Mapped provider transparent gate (before N8 so unsupported transparent preserves jpeg)
  if (next.background === 'transparent' && isTransparentUnavailableMapped(providerMapped, providerCaps)) {
    next.background = 'auto';
    pushReset('transparent_unsupported');
  }

  // N7: Chroma background rules (after N3 so valid svg/gif already cleared chroma -> auto)
  if (next.background?.startsWith('chroma_')) {
    if (ctx.editMode || isPromptOnlyApiPath(next.apiPath)) {
      next.background = 'auto';
      pushReset('chroma_reset');
    } else if (next.outputFormat !== 'png') {
      next.outputFormat = 'png';
      next.outputCompression = '';
      pushReset('chroma_to_png');
    }
  }

  // N8: Transparent background + JPEG -> PNG
  if (next.background === 'transparent' && next.outputFormat === 'jpeg') {
    next.outputFormat = 'png';
    next.outputCompression = '';
    pushReset('transparent_jpeg_to_png');
  }

  // N9: PNG clears outputCompression (last so any transition to png clears compression)
  if (next.outputFormat === 'png' && next.outputCompression !== '') {
    next.outputCompression = '';
    pushReset('compression_reset');
  }

  return { form: next, resets };
}

/** Builds the JSON request body for generation/edit submission from form state. */
export function buildRequestBody(form: PromptFormState): GenerateRequestBody {
  const quantity =
    form.quantity === '' ? DEFAULT_QUANTITY : Math.min(Math.max(Number(form.quantity) || DEFAULT_QUANTITY, 1), 10);
  const body: GenerateRequestBody = {
    prompt: form.prompt.trim(),
    size: form.size,
    model: form.model.trim(),
    n: quantity,
    quality: form.quality,
    output_format: form.outputFormat,
    output_compression: null,
    background: form.background,
    response_format:
      !isImage25(form.model) && !isImage26(form.model) && form.responseFormat
        ? (form.responseFormat as 'url' | 'b64_json')
        : null,
    api_path: form.apiPath
  };

  const special = form.outputFormat === 'svg' || form.outputFormat === 'gif';
  if (isImage26(form.model)) {
    delete body.response_format;
    body.api_path = '/v1/images/generations';
    if (form.complexity) body.complexity = form.complexity;
    if (form.outputFormat === 'gif') {
      body.frame_count = form.frameCount ?? 9;
      body.frame_duration_ms = form.frameDurationMs ?? 200;
    }
  }
  if (special) {
    body.output_compression = null;
    if (form.outputFormat === 'svg') body.n = 1;
    if (body.background?.startsWith('chroma_')) body.background = 'auto';
  }

  if (!special && form.outputFormat !== 'png' && form.outputCompression !== '') {
    body.output_compression = Math.min(Math.max(Number(form.outputCompression), 0), 100);
  }

  if (body.background === 'chroma_green' || body.background === 'chroma_magenta') {
    body.output_format = 'png';
    body.output_compression = null;
  } else if (body.background === 'transparent' && body.output_format === 'jpeg') {
    body.output_format = 'png';
    body.output_compression = null;
  }

  if (form.stream && !special) {
    body.stream = true;
    body.partial_images = Math.min(Math.max(Math.round(form.partialImages) || 2, 1), 3);
  }

  if (form.safetyIdentifier?.trim()) {
    body.safety_identifier = form.safetyIdentifier.trim();
  }

  return body;
}

/** Validates a built request body before submission, returning a typed error key or null. */
export function validateSubmission(
  body: GenerateRequestBody,
  { edit = false, hasMask = false }: ValidateSubmissionContext = {}
): SubmissionErrorKey | null {
  const special = body.output_format === 'svg' || body.output_format === 'gif';
  if (special && !isImage26(body.model)) return 'media_reset';
  // SVG is generation-only; GIF edits are fine until a mask is drawn.
  if (edit && (body.output_format === 'svg' || (body.output_format === 'gif' && hasMask))) return 'media_reset';
  if (
    body.output_format === 'gif' &&
    (!Number.isInteger(body.frame_count) ||
      Number(body.frame_count) < 2 ||
      Number(body.frame_count) > 64 ||
      !Number.isInteger(body.frame_duration_ms) ||
      Number(body.frame_duration_ms) < 20 ||
      Number(body.frame_duration_ms) > 2000 ||
      Number(body.frame_duration_ms) % 10 !== 0)
  ) {
    return 'invalid_frames';
  }
  if (!body.prompt) return 'prompt_required';
  if (promptLength(body.prompt) > MAX_PROMPT_CHARS) return 'prompt_too_long';
  if ((isImage25(body.model) || isImage26(body.model)) && !edit && body.api_path !== '/v1/images/generations') {
    return 'image25_endpoint';
  }
  if (!imageQualities(body.model).includes(body.quality)) return 'quality_reset';
  if ((edit || body.api_path === '/v1/images/generations') && !validOutputSize(body.size, body.output_format === 'gif')) {
    return 'invalid_size';
  }
  if (body.stream && !edit && !STREAM_SUPPORTED_PATHS.includes((body.api_path || '') as ApiPath)) {
    return 'stream_unsupported_path';
  }
  if (!edit && body.background?.startsWith('chroma_') && body.api_path !== '/v1/images/generations') {
    return 'chroma_unsupported_path';
  }
  return null;
}
