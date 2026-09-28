import { describe, expect, it } from 'vitest';
import {
  BUILT_IN_SIZES,
  MAX_NAMED_SIZE_PRESETS,
  addNamedSizePreset,
  groupSizesByTier,
  sanitizeNamedSizePresets,
  sizeTier
} from '$lib/utils/sizePresets';
import { validImageSize } from '$lib/utils/imageModels';
import { moveCollectionId } from '$lib/stores/galleryCollections';

describe('sizeTier', () => {
  it('groups by the longest side', () => {
    expect(sizeTier('1024x1024')).toBe('1K');
    expect(sizeTier('1536x1024')).toBe('1K');
    expect(sizeTier('2048x2048')).toBe('2K');
    expect(sizeTier('1440x2560')).toBe('2K');
    expect(sizeTier('3072x2048')).toBe('2K');
    expect(sizeTier('3840x2160')).toBe('4K');
    expect(sizeTier('auto')).toBeNull();
  });

  it('only ships valid built-in sizes, each in a tier', () => {
    expect(BUILT_IN_SIZES.every(validImageSize)).toBe(true);
    const groups = groupSizesByTier(BUILT_IN_SIZES);
    expect(groups['1K']).toEqual(['1024x1024', '1024x1536', '1536x1024']);
    expect(groups['4K']).toEqual(['3840x2160', '2160x3840']);
    expect(groups['1K'].length + groups['2K'].length + groups['4K'].length).toBe(BUILT_IN_SIZES.length);
  });
});

describe('named size presets', () => {
  it('adds a trimmed, validated preset', () => {
    const result = addNamedSizePreset([], '  Phone   wallpaper ', '1152X2048');
    expect(result).toEqual({ ok: true, presets: [{ name: 'Phone wallpaper', size: '1152x2048' }] });
  });

  it('rejects empty names, invalid sizes, duplicates and overflow', () => {
    const existing = [{ name: 'Poster', size: '1024x1536' }];
    expect(addNamedSizePreset(existing, ' ', '1024x1024')).toEqual({ ok: false, reason: 'name' });
    expect(addNamedSizePreset(existing, 'Bad', '1000x1000')).toEqual({ ok: false, reason: 'size' });
    expect(addNamedSizePreset(existing, 'Auto', 'auto')).toEqual({ ok: false, reason: 'size' });
    expect(addNamedSizePreset(existing, 'poster', '1024x1024')).toEqual({ ok: false, reason: 'duplicate' });
    const full = Array.from({ length: MAX_NAMED_SIZE_PRESETS }, (_, index) => ({ name: `p${index}`, size: '1024x1024' }));
    expect(addNamedSizePreset(full, 'extra', '1024x1024')).toEqual({ ok: false, reason: 'limit' });
  });

  it('sanitizes stored presets', () => {
    expect(
      sanitizeNamedSizePresets([
        { name: 'Square', size: '1024x1024' },
        { name: 'square', size: '2048x2048' },
        { name: 'Broken', size: '17x17' },
        { name: '', size: '1024x1024' },
        'junk'
      ])
    ).toEqual([{ name: 'Square', size: '1024x1024' }]);
    expect(sanitizeNamedSizePresets(null)).toEqual([]);
  });
});

describe('moveCollectionId', () => {
  it('moves an id and ignores out-of-range moves', () => {
    const ids = ['a', 'b', 'c'];
    expect(moveCollectionId(ids, 0, 2)).toEqual(['b', 'c', 'a']);
    expect(moveCollectionId(ids, 2, 0)).toEqual(['c', 'a', 'b']);
    expect(moveCollectionId(ids, 1, 1)).toBe(ids);
    expect(moveCollectionId(ids, 0, 3)).toBe(ids);
  });
});
