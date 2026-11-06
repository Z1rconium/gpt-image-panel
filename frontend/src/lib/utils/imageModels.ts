// Keep aligned with backend/app/core/image_models.py; verified 2026-09-10.
export const MAX_PROMPT_CHARS = 32000;
export const IMAGE_MODEL_PRESETS = ['gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst', 'gpt-image-2.6-flare', 'gpt-image-2.6-sunburst'];
export type ImageQuality = 'auto' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
const BASE_QUALITIES: ImageQuality[] = ['auto', 'low', 'medium', 'high'];
const IMAGE_25_MODELS = new Set([
  'gpt-image-2.5-flare', 'gpt-image-2.5-flare-2026-09-08',
  'gpt-image-2.5-sunburst', 'gpt-image-2.5-sunburst-2026-09-08'
]);
const IMAGE_26_MODELS = new Set([
  'gpt-image-2.6-flare', 'gpt-image-2.6-flare-2026-11-04',
  'gpt-image-2.6-sunburst', 'gpt-image-2.6-sunburst-2026-11-04'
]);

export function isImage26(model: string | null | undefined): boolean {
  return IMAGE_26_MODELS.has((model || '').trim());
}

export function isImage25(model: string | null | undefined): boolean {
  return IMAGE_25_MODELS.has((model || '').trim());
}

export function imageQualities(model: string): ImageQuality[] {
  return (isImage25(model) || isImage26(model)) ? [...BASE_QUALITIES, 'xhigh', 'max'] : BASE_QUALITIES;
}

export function promptLength(prompt: string): number {
  return Array.from(prompt).length;
}

export function validImageSize(size: string): boolean {
  return validOutputSize(size, false);
}

export function validOutputSize(size: string, animated = false): boolean {
  if (size === 'auto') return true;
  const match = /^(\d+)x(\d+)$/i.exec(size);
  if (!match) return false;
  const width = Number(match[1]);
  const height = Number(match[2]);
  return width > 0 && height > 0 && Math.max(width, height) <= 3840
    && width % 16 === 0 && height % 16 === 0
    && Math.max(width, height) / Math.min(width, height) <= 3
    && (animated || width * height >= 655360) && width * height <= 8294400;
}
