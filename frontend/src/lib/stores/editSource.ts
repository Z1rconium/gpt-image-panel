import { get, writable } from 'svelte/store';
import { t } from '$lib/i18n';

export const MAX_EDIT_SOURCE_IMAGES = 16;

export type EditUploadSource = {
  id: string;
  file: File;
  label: string;
  previewUrl: string;
  previewLabel: string;
  // 0 until the async size probe in addFiles() resolves; callers that need
  // the size (e.g. restoreMaskFromJob's primary/mask dimension check) treat 0
  // as "unknown" and fall back to decoding the file themselves.
  width: number;
  height: number;
};

export type EditMaskOrigin = 'painted' | 'uploaded' | 'restored';

export type EditMask = {
  sourceId: string;
  blob: Blob;
  width: number;
  height: number;
  coverage: number;
  previewUrl: string;
  origin: EditMaskOrigin;
};

export type EditSourceState = {
  files: EditUploadSource[];
  selectedGalleryImageId: string;
  galleryLabel: string;
  galleryPreviewUrl: string;
  galleryPreviewLabel: string;
  // 0 when the gallery entry predates image_width/image_height (legacy rows);
  // same "unknown, decode if you need it" contract as EditUploadSource above.
  galleryWidth: number;
  galleryHeight: number;
  mask: EditMask | null;
};

export const initialEditSourceState: EditSourceState = {
  files: [],
  selectedGalleryImageId: '',
  galleryLabel: '',
  galleryPreviewUrl: '',
  galleryPreviewLabel: '',
  galleryWidth: 0,
  galleryHeight: 0,
  mask: null
};

let nextEditSourceId = 0;

// Bumped whenever a mutation invalidates the mask by moving the primary image.
// Workspace subscribes so it can warn once without every caller threading a
// return flag through its own signature.
export const editMaskDiscards = writable(0);

export function primaryEditSourceId(source: EditSourceState) {
  return source.selectedGalleryImageId || source.files[0]?.id || '';
}

export function isMaskValid(source: EditSourceState) {
  return Boolean(source.mask && source.mask.sourceId === primaryEditSourceId(source));
}

function isImageFile(file: File) {
  if (file.type.startsWith('image/') && file.type !== 'image/svg+xml') return true;
  return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i.test(file.name);
}

function revokeEditSourceUrls(source: EditSourceState) {
  source.files.forEach((upload) => URL.revokeObjectURL(upload.previewUrl));
}

function revokeMaskUrl(mask: EditMask | null) {
  if (mask) URL.revokeObjectURL(mask.previewUrl);
}

// The mask is bound to the primary image: only a change of primary invalidates
// it, so adding or removing a reference image must not discard the user's work.
function dropStaleMask(next: EditSourceState): EditSourceState {
  if (next.mask && next.mask.sourceId !== primaryEditSourceId(next)) {
    revokeMaskUrl(next.mask);
    editMaskDiscards.update((count) => count + 1);
    return { ...next, mask: null };
  }
  return next;
}

function makeUploadSource(file: File): EditUploadSource {
  const objectUrl = URL.createObjectURL(file);
  nextEditSourceId += 1;
  return {
    id: `upload-${Date.now()}-${nextEditSourceId}`,
    file,
    label: file.name,
    previewUrl: objectUrl,
    previewLabel: file.name,
    width: 0,
    height: 0
  };
}

/** Decode just enough to learn the size, without keeping the bitmap around. */
async function probeImageSize(file: File): Promise<{ width: number; height: number } | null> {
  if (typeof createImageBitmap !== 'function') return null;
  try {
    const bitmap = await createImageBitmap(file);
    const size = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return size;
  } catch {
    return null;
  }
}

export function editSourceCount(source: EditSourceState) {
  return source.files.length + (source.selectedGalleryImageId ? 1 : 0);
}

function createEditSourceStore() {
  const { subscribe, set, update } = writable<EditSourceState>(initialEditSourceState);
  let state = initialEditSourceState;

  subscribe((value) => {
    state = value;
  });

  function addFiles(files: File[], setError: (message: string) => void) {
    const validFiles = files.filter(isImageFile);
    const invalidCount = files.length - validFiles.length;
    if (!validFiles.length) {
      if (files.length) setError(get(t).messages.imageUploadRequired);
      return 0;
    }

    const current = state;
    const availableSlots = MAX_EDIT_SOURCE_IMAGES - editSourceCount(current);
    if (availableSlots <= 0) {
      setError(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
      return 0;
    }

    const acceptedFiles = validFiles.slice(0, availableSlots);
    const overLimitCount = validFiles.length - acceptedFiles.length;
    if (invalidCount > 0) setError(get(t).messages.imageUploadRequired);
    if (overLimitCount > 0) setError(get(t).messages.editSourceSomeSkipped(MAX_EDIT_SOURCE_IMAGES));

    const nextUploads = acceptedFiles.map(makeUploadSource);
    update((source) =>
      dropStaleMask({
        ...source,
        files: [...source.files, ...nextUploads]
      })
    );
    for (const upload of nextUploads) {
      void probeImageSize(upload.file).then((size) => {
        if (size) updateUploadSize(upload.id, size.width, size.height);
      });
    }
    return nextUploads.length;
  }

  function updateUploadSize(sourceId: string, width: number, height: number) {
    update((source) => ({
      ...source,
      files: source.files.map((item) => (item.id === sourceId ? { ...item, width, height } : item))
    }));
  }

  return {
    subscribe,
    set,
    update,
    addFiles,
    updateUploadSize,
    clear(input?: HTMLInputElement) {
      revokeEditSourceUrls(state);
      if (state.mask) editMaskDiscards.update((count) => count + 1);
      revokeMaskUrl(state.mask);
      set({ ...initialEditSourceState, files: [] });
      if (input) input.value = '';
    },
    handleFile(event: Event, setError: (message: string) => void, input?: HTMLInputElement) {
      const target = event.currentTarget as HTMLInputElement;
      const selectedFiles = Array.from(target.files || []);
      addFiles(selectedFiles, setError);
      if (input) input.value = '';
      else target.value = '';
    },
    setGallerySource(
      imageId: string,
      label: string,
      previewUrl: string,
      previewLabel: string,
      setError?: (message: string) => void,
      width = 0,
      height = 0
    ) {
      const current = state;
      if (!current.selectedGalleryImageId && editSourceCount(current) >= MAX_EDIT_SOURCE_IMAGES) {
        setError?.(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
        return false;
      }

      update((source) =>
        dropStaleMask({
          ...source,
          selectedGalleryImageId: imageId,
          galleryLabel: label,
          galleryPreviewUrl: previewUrl,
          galleryPreviewLabel: previewLabel,
          galleryWidth: width,
          galleryHeight: height
        })
      );
      return true;
    },
    clearGallerySource(imageId?: string) {
      update((source) => {
        if (imageId && source.selectedGalleryImageId !== imageId) return source;
        return dropStaleMask({
          ...source,
          selectedGalleryImageId: '',
          galleryLabel: '',
          galleryPreviewUrl: '',
          galleryPreviewLabel: '',
          galleryWidth: 0,
          galleryHeight: 0
        });
      });
    },
    remove(sourceId: string) {
      const upload = state.files.find((source) => source.id === sourceId);
      if (upload) {
        URL.revokeObjectURL(upload.previewUrl);
        update((source) =>
          dropStaleMask({
            ...source,
            files: source.files.filter((item) => item.id !== sourceId)
          })
        );
        return true;
      }
      if (state.selectedGalleryImageId === sourceId) {
        update((source) =>
          dropStaleMask({
            ...source,
            selectedGalleryImageId: '',
            galleryLabel: '',
            galleryPreviewUrl: '',
            galleryPreviewLabel: '',
            galleryWidth: 0,
            galleryHeight: 0
          })
        );
        return true;
      }
      return false;
    },
    setMask(mask: EditMask) {
      revokeMaskUrl(state.mask);
      update((source) => ({ ...source, mask }));
    },
    removeMask() {
      revokeMaskUrl(state.mask);
      update((source) => ({ ...source, mask: null }));
    },
    cleanup() {
      revokeEditSourceUrls(state);
      revokeMaskUrl(state.mask);
    }
  };
}

export const editSourceStore = createEditSourceStore();
