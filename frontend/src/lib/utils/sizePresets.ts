import { validImageSize } from '$lib/utils/imageModels';

export type SizeTier = '1K' | '2K' | '4K';

export type NamedSizePreset = {
  name: string;
  size: string;
};

export const MAX_NAMED_SIZE_PRESETS = 20;
export const MAX_SIZE_PRESET_NAME_LENGTH = 40;

export const SIZE_TIERS: SizeTier[] = ['1K', '2K', '4K'];

export const BUILT_IN_SIZES = [
  '1024x1024',
  '1024x1536',
  '1536x1024',
  '2048x2048',
  '2048x3072',
  '3072x2048',
  '2560x1440',
  '1440x2560',
  '3840x2160',
  '2160x3840'
];

/** Tier by longest side: up to 1536 is 1K, up to 3072 is 2K, larger is 4K. */
export function sizeTier(size: string): SizeTier | null {
  const match = /^(\d+)x(\d+)$/i.exec(size.trim());
  if (!match) return null;
  const longest = Math.max(Number(match[1]), Number(match[2]));
  if (longest <= 1536) return '1K';
  if (longest <= 3072) return '2K';
  return '4K';
}

export function groupSizesByTier(sizes: string[]): Record<SizeTier, string[]> {
  const groups: Record<SizeTier, string[]> = { '1K': [], '2K': [], '4K': [] };
  for (const size of sizes) {
    const tier = sizeTier(size);
    if (tier) groups[tier].push(size);
  }
  return groups;
}

export function normalizeSizePresetName(name: string): string {
  return name.replace(/\s+/g, ' ').trim().slice(0, MAX_SIZE_PRESET_NAME_LENGTH);
}

/** Drops invalid, duplicate-named and overflow entries from stored presets. */
export function sanitizeNamedSizePresets(value: unknown): NamedSizePreset[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const presets: NamedSizePreset[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const name = normalizeSizePresetName(String((item as NamedSizePreset).name ?? ''));
    const size = String((item as NamedSizePreset).size ?? '').trim().toLowerCase();
    const key = name.toLocaleLowerCase();
    if (!name || size === 'auto' || !validImageSize(size) || seen.has(key)) continue;
    seen.add(key);
    presets.push({ name, size });
    if (presets.length >= MAX_NAMED_SIZE_PRESETS) break;
  }
  return presets;
}

export type AddSizePresetResult =
  | { ok: true; presets: NamedSizePreset[] }
  | { ok: false; reason: 'name' | 'size' | 'duplicate' | 'limit' };

export function addNamedSizePreset(presets: NamedSizePreset[], name: string, size: string): AddSizePresetResult {
  const normalizedName = normalizeSizePresetName(name);
  const normalizedSize = size.trim().toLowerCase();
  if (!normalizedName) return { ok: false, reason: 'name' };
  if (normalizedSize === 'auto' || !validImageSize(normalizedSize)) return { ok: false, reason: 'size' };
  if (presets.some((preset) => preset.name.toLocaleLowerCase() === normalizedName.toLocaleLowerCase())) {
    return { ok: false, reason: 'duplicate' };
  }
  if (presets.length >= MAX_NAMED_SIZE_PRESETS) return { ok: false, reason: 'limit' };
  return { ok: true, presets: [...presets, { name: normalizedName, size: normalizedSize }] };
}
