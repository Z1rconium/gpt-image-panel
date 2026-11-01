import { writable } from 'svelte/store';
import type { GalleryEntry } from '$lib/api/types/gallery';

export type LightboxState = {
  image: GalleryEntry | null;
};

function createLightboxStore() {
  const { subscribe, update, set } = writable<LightboxState>({ image: null });

  return {
    subscribe,
    open(image: GalleryEntry) {
      set({ image });
    },
    setImage(image: GalleryEntry | null) {
      set({ image });
    },
    updateFavorite(imageId: string, favorite: boolean) {
      update((current) => ({
        image: current.image?.id === imageId ? { ...current.image, favorite } : current.image
      }));
    }
  };
}

export const lightboxStore = createLightboxStore();
