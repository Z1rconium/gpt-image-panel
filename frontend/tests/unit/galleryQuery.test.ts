import { describe, expect, it } from 'vitest';
import {
  buildGalleryParams,
  defaultGalleryFilters,
  galleryFiltersToSelectionPayload,
  gallerySearchBody,
  pendingImageMatchesFilters,
  sameGalleryFilters
} from '$lib/features/gallery/query';
import type { GalleryEntry } from '$lib/api/types/gallery';

function entry(overrides: Partial<GalleryEntry> = {}): GalleryEntry {
  return {
    id: 'img-1',
    prompt: 'A calm lake',
    filename: 'img-1.png',
    model: 'gpt-image-2',
    size: '1024x1024',
    favorite: false,
    created_at: '2026-05-18T12:00:00Z',
    completed_at: '2026-05-18T20:00:00+08:00',
    api_preset_name: 'Default',
    ...overrides
  } as GalleryEntry;
}

describe('buildGalleryParams', () => {
  it('omits empty filters and always includes page/page_size', () => {
    const params = buildGalleryParams(2, defaultGalleryFilters);
    expect(params.get('page')).toBe('2');
    expect(params.get('page_size')).toBe('9');
    expect(params.get('model')).toBeNull();
    expect(params.get('favorite')).toBeNull();
  });

  it('serializes active filters and cursors', () => {
    const params = buildGalleryParams(
      1,
      { ...defaultGalleryFilters, model: 'gpt-image-2', favorite: true, prompt: 'lake' },
      true,
      'cursor-1',
      'next',
      false,
      false
    );
    expect(params.get('model')).toBe('gpt-image-2');
    expect(params.get('favorite')).toBe('true');
    expect(params.get('include_total_bytes')).toBe('true');
    expect(params.get('include_counts')).toBe('false');
    expect(params.get('include_filter_options')).toBe('false');
    expect(params.get('cursor')).toBe('cursor-1');
    expect(params.get('direction')).toBe('next');
  });
});

describe('gallerySearchBody', () => {
  it('trims the prompt and maps favorite to a boolean or null', () => {
    const params = buildGalleryParams(1, { ...defaultGalleryFilters, prompt: '  lake  ', favorite: true });
    const body = gallerySearchBody(params, { ...defaultGalleryFilters, prompt: '  lake  ', favorite: true });
    expect(body.prompt).toBe('lake');
    expect(body.favorite).toBe(true);
    expect(body.direction).toBe('next');
  });
});

describe('galleryFiltersToSelectionPayload', () => {
  it('mirrors the search filter shape', () => {
    const payload = galleryFiltersToSelectionPayload({ ...defaultGalleryFilters, model: 'm', favorite: true });
    expect(payload).toMatchObject({ model: 'm', favorite: true, prompt: '' });
  });
});

describe('sameGalleryFilters', () => {
  it('detects equality and a changed field', () => {
    const a = { ...defaultGalleryFilters, model: 'm' };
    expect(sameGalleryFilters(a, { ...a })).toBe(true);
    expect(sameGalleryFilters(a, { ...a, model: 'n' })).toBe(false);
  });
});

describe('pendingImageMatchesFilters', () => {
  it('matches prompt, model, size, favorite and date ranges', () => {
    const image = entry({ favorite: true });
    expect(pendingImageMatchesFilters(image, { ...defaultGalleryFilters, prompt: 'lake', favorite: true })).toBe(true);
    expect(pendingImageMatchesFilters(image, { ...defaultGalleryFilters, model: 'other' })).toBe(false);
    expect(pendingImageMatchesFilters(image, { ...defaultGalleryFilters, dateFrom: '2026-05-19' })).toBe(false);
    expect(pendingImageMatchesFilters(image, { ...defaultGalleryFilters, dateTo: '2026-05-17' })).toBe(false);
  });
});
