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

describe('shared gallery filter wire contract', () => {
  it('serializes the exact empty search and selection shapes', () => {
    const filters = { ...defaultGalleryFilters, prompt: '   ' };
    const expected = {
      prompt: '', model: '', preset: '', size: '', date_from: '', date_to: '',
      favorite: null, mask_only: null, collection_id: null
    };
    expect(galleryFiltersToSelectionPayload(filters)).toEqual(expected);
    expect(gallerySearchBody(new URLSearchParams(), filters)).toEqual({
      page: 1, page_size: 9, ...expected, include_total_bytes: false,
      include_counts: true, include_filter_options: true, cursor: null, direction: 'next'
    });
  });

  it('preserves untrimmed non-prompt filters and explicit pagination controls', () => {
    const filters = {
      prompt: '  lake  ', model: ' model ', preset: ' preset ', size: '1024x1536',
      dateFrom: '2026-01-01', dateTo: '2026-12-31', favorite: true, maskOnly: true, collectionId: 'album-1'
    };
    const expected = {
      prompt: 'lake', model: ' model ', preset: ' preset ', size: '1024x1536',
      date_from: '2026-01-01', date_to: '2026-12-31', favorite: true, mask_only: true, collection_id: 'album-1'
    };
    expect(galleryFiltersToSelectionPayload(filters)).toEqual(expected);
    const body = gallerySearchBody(buildGalleryParams(3, filters, true, 'cursor-1', 'prev', false, false), filters);
    expect(body).toEqual({
      page: 3, page_size: 9, ...expected, include_total_bytes: true,
      include_counts: false, include_filter_options: false, cursor: 'cursor-1', direction: 'prev'
    });
    const { page, page_size, include_total_bytes, include_counts, include_filter_options, cursor, direction, ...filterBody } = body;
    expect(filterBody).toEqual(galleryFiltersToSelectionPayload(filters));
  });

  it('keeps the existing numeric and string parameter coercion', () => {
    const body = gallerySearchBody(new URLSearchParams({
      page: '0', page_size: '12', cursor: '', direction: '', include_total_bytes: '1', include_counts: '0'
    }), defaultGalleryFilters);
    expect(body).toMatchObject({ page: 0, page_size: 12, cursor: '', direction: 'next', include_total_bytes: false, include_counts: true });
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

describe('collection filter', () => {
  it('serializes collection_id in params, search body and selection payload', () => {
    const filters = { ...defaultGalleryFilters, collectionId: 'album-1' };
    const params = buildGalleryParams(1, filters);
    expect(params.get('collection_id')).toBe('album-1');
    expect(gallerySearchBody(params, filters).collection_id).toBe('album-1');
    expect(galleryFiltersToSelectionPayload(filters).collection_id).toBe('album-1');
    expect(gallerySearchBody(buildGalleryParams(1, defaultGalleryFilters), defaultGalleryFilters).collection_id).toBeNull();
    expect(sameGalleryFilters(filters, { ...filters, collectionId: 'album-2' })).toBe(false);
  });

  it('never treats a pending image as a collection member', () => {
    expect(pendingImageMatchesFilters(entry(), { ...defaultGalleryFilters, collectionId: 'album-1' })).toBe(false);
  });
});
