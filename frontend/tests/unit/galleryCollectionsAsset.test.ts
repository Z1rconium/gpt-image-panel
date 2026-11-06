import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { GalleryCollection, GalleryCollectionReferencesResponse } from '$lib/api/types/gallery';

const api = vi.hoisted(() => ({
  apiFetch: vi.fn()
}));

vi.mock('$lib/api/client', async (original) => ({
  ...(await original<typeof import('$lib/api/client')>()),
  apiFetch: api.apiFetch
}));

import { galleryCollectionsStore } from '$lib/stores/galleryCollections';

describe('galleryCollectionsStore asset pack features', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sets asset_alias on a collection and updates store', async () => {
    const initialCollection: GalleryCollection = {
      id: 'col-1',
      name: 'Characters',
      is_default: false,
      image_count: 5,
      position: 0,
      asset_alias: null,
      created_at: '2026-11-06T00:00:00Z',
      updated_at: '2026-11-06T00:00:00Z'
    };

    api.apiFetch.mockResolvedValueOnce([initialCollection]);
    await galleryCollectionsStore.load(true);

    const updatedCollection: GalleryCollection = {
      ...initialCollection,
      asset_alias: 'char_pack'
    };

    api.apiFetch.mockResolvedValueOnce(updatedCollection);
    const result = await galleryCollectionsStore.setAlias('col-1', '  char_pack  ');

    expect(api.apiFetch).toHaveBeenCalledWith(
      '/api/gallery/collections/col-1',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_alias: 'char_pack' })
      },
      'updating collection alias'
    );
    expect(result.asset_alias).toBe('char_pack');

    const state = get(galleryCollectionsStore);
    const item = state.collections.find((c) => c.id === 'col-1');
    expect(item?.asset_alias).toBe('char_pack');
  });

  it('clears asset_alias when null or blank string passed', async () => {
    const initialCollection: GalleryCollection = {
      id: 'col-2',
      name: 'Styles',
      is_default: false,
      image_count: 3,
      position: 1,
      asset_alias: 'style_pack',
      created_at: '2026-11-06T00:00:00Z',
      updated_at: '2026-11-06T00:00:00Z'
    };

    api.apiFetch.mockResolvedValueOnce([initialCollection]);
    await galleryCollectionsStore.load(true);

    const updatedCollection: GalleryCollection = {
      ...initialCollection,
      asset_alias: null
    };

    api.apiFetch.mockResolvedValueOnce(updatedCollection);
    await galleryCollectionsStore.setAlias('col-2', '');

    expect(api.apiFetch).toHaveBeenCalledWith(
      '/api/gallery/collections/col-2',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_alias: null })
      },
      'updating collection alias'
    );
  });

  it('loads collection references excluding unsupported types metadata', async () => {
    const mockReferencesResponse: GalleryCollectionReferencesResponse = {
      collection: {
        id: 'col-1',
        name: 'Characters',
        is_default: false,
        image_count: 3,
        position: 0,
        asset_alias: 'chars',
        created_at: '2026-11-06T00:00:00Z',
        updated_at: '2026-11-06T00:00:00Z'
      },
      images: [
        {
          id: 'img-1',
          prompt: 'char prompt',
          size: '1024x1024',
          favorite: false,
          filename: 'char1.png',
          created_at: '2026-11-06T00:00:00Z',
          image_width: 512,
          image_height: 512,
          image_url: '/gallery/img-1.png'
        }
      ],
      total_count: 3,
      skipped_unsupported: 2
    };

    api.apiFetch.mockResolvedValueOnce(mockReferencesResponse);
    const res = await galleryCollectionsStore.loadReferences('col-1');

    expect(api.apiFetch).toHaveBeenCalledWith(
      '/api/gallery/collections/col-1/references',
      {},
      'loading collection references'
    );
    expect(res.images.length).toBe(1);
    expect(res.skipped_unsupported).toBe(2);
  });

  it('reorders collection items and updates store', async () => {
    const initialCollection: GalleryCollection = {
      id: 'col-1',
      name: 'Characters',
      is_default: false,
      image_count: 2,
      position: 0,
      asset_alias: 'chars',
      created_at: '2026-11-06T00:00:00Z',
      updated_at: '2026-11-06T00:00:00Z'
    };

    api.apiFetch.mockResolvedValueOnce([initialCollection]);
    await galleryCollectionsStore.load(true);

    const reorderedCollection: GalleryCollection = {
      ...initialCollection,
      updated_at: '2026-11-06T01:00:00Z'
    };

    api.apiFetch.mockResolvedValueOnce(reorderedCollection);
    await galleryCollectionsStore.reorderItems('col-1', ['img-2', 'img-1']);

    expect(api.apiFetch).toHaveBeenCalledWith(
      '/api/gallery/collections/col-1/items/order',
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ['img-2', 'img-1'] })
      },
      'reordering collection items'
    );
  });
});
