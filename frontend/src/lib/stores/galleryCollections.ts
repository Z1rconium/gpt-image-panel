import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import type {
  GalleryCollection,
  GalleryCollectionItemsResponse,
  GalleryImageCollectionsResponse
} from '$lib/api/types/gallery';
import { galleryStore } from '$lib/stores/gallery';

export type GalleryCollectionsState = {
  collections: GalleryCollection[];
  loaded: boolean;
  loading: boolean;
};

export type CollectionItemsTarget = { ids: string[] } | { selection_token: string };

const initialState: GalleryCollectionsState = {
  collections: [],
  loaded: false,
  loading: false
};

function jsonRequest(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  };
}

function createGalleryCollectionsStore() {
  const { subscribe, update } = writable<GalleryCollectionsState>(initialState);
  let loadPromise: Promise<void> | null = null;

  function replaceCollection(collection: GalleryCollection) {
    update((current) => ({
      ...current,
      collections: current.collections.map((item) =>
        item.id === collection.id
          ? collection
          : collection.is_default && item.is_default
            ? { ...item, is_default: false }
            : item
      )
    }));
  }

  async function load(force = false) {
    if (!force && get({ subscribe }).loaded) return;
    if (loadPromise) return loadPromise;
    update((current) => ({ ...current, loading: true }));
    loadPromise = (async () => {
      try {
        const collections = await apiFetch<GalleryCollection[]>('/api/gallery/collections', {}, 'loading collections');
        update(() => ({ collections, loaded: true, loading: false }));
      } catch (error) {
        update((current) => ({ ...current, loading: false }));
        throw error;
      } finally {
        loadPromise = null;
      }
    })();
    return loadPromise;
  }

  async function create(name: string) {
    const collection = await apiFetch<GalleryCollection>(
      '/api/gallery/collections',
      jsonRequest('POST', { name }),
      'creating collection'
    );
    update((current) => ({ ...current, collections: [...current.collections, collection] }));
    return collection;
  }

  async function rename(id: string, name: string) {
    const collection = await apiFetch<GalleryCollection>(
      `/api/gallery/collections/${encodeURIComponent(id)}`,
      jsonRequest('PATCH', { name }),
      'renaming collection'
    );
    replaceCollection(collection);
    return collection;
  }

  async function setDefault(id: string, isDefault: boolean) {
    const collection = await apiFetch<GalleryCollection>(
      `/api/gallery/collections/${encodeURIComponent(id)}`,
      jsonRequest('PATCH', { is_default: isDefault }),
      'updating default collection'
    );
    replaceCollection(collection);
    return collection;
  }

  async function remove(id: string) {
    await apiFetch(`/api/gallery/collections/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'deleting collection');
    update((current) => ({ ...current, collections: current.collections.filter((item) => item.id !== id) }));
    if (get(galleryStore).filters.collectionId === id) galleryStore.updateFilter('collectionId', '');
  }

  /** Optimistic reorder; rolls back to the server order if the save fails. */
  async function reorder(ids: string[]) {
    const previous = get({ subscribe }).collections;
    const byId = new Map(previous.map((item) => [item.id, item]));
    update((current) => ({
      ...current,
      collections: ids.flatMap((id, position) => {
        const item = byId.get(id);
        return item ? [{ ...item, position }] : [];
      })
    }));
    try {
      const collections = await apiFetch<GalleryCollection[]>(
        '/api/gallery/collections/order',
        jsonRequest('PUT', { ids }),
        'reordering collections'
      );
      update((current) => ({ ...current, collections }));
    } catch (error) {
      update((current) => ({ ...current, collections: previous }));
      throw error;
    }
  }

  async function changeItems(id: string, target: CollectionItemsTarget, add: boolean) {
    const result = await apiFetch<GalleryCollectionItemsResponse>(
      `/api/gallery/collections/${encodeURIComponent(id)}/items${add ? '' : '/remove'}`,
      jsonRequest('POST', target),
      add ? 'adding images to collection' : 'removing images from collection'
    );
    replaceCollection(result.collection);
    const galleryState = get(galleryStore);
    if (result.changed_count && galleryState.filters.collectionId === id) {
      await galleryStore.loadGallery(galleryState.page);
    }
    return result;
  }

  function addItems(id: string, target: CollectionItemsTarget) {
    return changeItems(id, target, true);
  }

  function removeItems(id: string, target: CollectionItemsTarget) {
    return changeItems(id, target, false);
  }

  async function imageCollectionIds(imageId: string) {
    const result = await apiFetch<GalleryImageCollectionsResponse>(
      `/api/gallery/${encodeURIComponent(imageId)}/collections`,
      {},
      'loading image collections'
    );
    return result.collection_ids;
  }

  return { subscribe, load, create, rename, setDefault, remove, reorder, addItems, removeItems, imageCollectionIds };
}

export const galleryCollectionsStore = createGalleryCollectionsStore();

export function defaultCollection(collections: GalleryCollection[]) {
  return collections.find((collection) => collection.is_default) || null;
}

export function moveCollectionId(ids: string[], fromIndex: number, toIndex: number) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= ids.length || toIndex >= ids.length) {
    return ids;
  }
  const next = [...ids];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  return next;
}
