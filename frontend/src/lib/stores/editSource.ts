import { get, writable } from 'svelte/store';
import { t } from '$lib/i18n';
// Type-only: erased at compile time, so this does not pull the mask editor's
// code into whatever eagerly imports this store (see maskImage.ts's header).
import type { MaskRegionPreferences } from '$lib/features/mask/maskRegion';
// Type-only as well: the markup editor's drawing code must not be pulled into
// the homepage graph by the store that carries its browser-side state.
import type { MarkupEditorState } from '$lib/features/markup/markupDocument';

export const MAX_EDIT_SOURCE_IMAGES = 16;

type EditSourceBase = {
  id: string;
  label: string;
  previewUrl: string;
  previewLabel: string;
  // 0 means unknown: for uploads until the async size probe in addFiles()
  // resolves, for gallery entries when the row predates image_width/height.
  // Callers that need the size (e.g. restoreMaskFromJob's primary/mask
  // dimension check) fall back to decoding the image themselves.
  width: number;
  height: number;
};

export type EditUploadSource = EditSourceBase & { kind: 'upload'; file: File };

export type EditMaskOrigin = 'painted' | 'uploaded' | 'restored';

// Reopening the editor with only `blob` (the already-processed export) forces
// the non-idempotent options off and drops the original strokes; carrying
// this alongside it lets `MaskEditorDialog` restore the exact pre-export
// state instead (plan Phase 4, U2). It never leaves the browser: nothing here
// is read by the upload path (see stores/preview.ts), only by the editor
// itself, so it is fine for it to hold blobs no server ever sees.
export type EditMaskEditorState = {
  marks: Blob;
  protected: Blob | null;
  smoothPx: number;
  region: MaskRegionPreferences;
};

export type EditMask = {
  sourceId: string;
  blob: Blob;
  width: number;
  height: number;
  coverage: number;
  previewUrl: string;
  origin: EditMaskOrigin;
  editorState?: EditMaskEditorState;
};

export type EditMarkupOrigin = 'painted' | 'uploaded' | 'restored';

// `blob` is the only thing that is uploaded. `editorState` lets the same page
// session resume drawing; it is browser-only, immutable, and optional, so every
// pre-existing `{sourceId, blob, previewUrl, width, height}` caller stays valid.
export type EditMarkup = {
  sourceId: string;
  blob: Blob;
  previewUrl: string;
  width: number;
  height: number;
  origin?: EditMarkupOrigin;
  editorState?: MarkupEditorState;
};

// What callers hand to addGalleryReferences(); the store stamps the kind.
export type EditGalleryReferenceSource = {
  id: string;
  imageId: string;
  filename?: string;
  label: string;
  previewUrl: string;
  previewLabel: string;
  width: number;
  height: number;
};

// `gallery_primary` is the single gallery image an edit starts from (its id is
// the gallery image id, and it selects the /from-gallery endpoint);
// `gallery_reference` images only ride along as extra references.
export type EditGallerySource = EditSourceBase & {
  kind: 'gallery_primary' | 'gallery_reference';
  imageId: string;
  filename?: string;
};

export type EditSource = EditUploadSource | EditGallerySource;

// `sources` is always in canonical order: the gallery primary (at most one),
// then uploads, then gallery references, each group in insertion order. That is
// also the order the request is built in, and it makes the primary image
// simply `sources[0]`. Only editSourceStore's commit() may reorder it.
export type EditSourceState = {
  sources: EditSource[];
  markup?: EditMarkup | null;
  mask: EditMask | null;
};

export const initialEditSourceState: EditSourceState = {
  sources: [],
  markup: null,
  mask: null
};

let nextEditSourceId = 0;

// Bumped whenever a mutation invalidates the mask by moving the primary image.
// Workspace subscribes so it can warn once without every caller threading a
// return flag through its own signature.
export const editMaskDiscards = writable(0);
// Same idea for the markup layer, which is also bound to the primary image.
export const editMarkupDiscards = writable(0);

export function primaryEditSource(source: EditSourceState): EditSource | null {
  return source.sources[0] ?? null;
}

export function primaryEditSourceId(source: EditSourceState) {
  return primaryEditSource(source)?.id ?? '';
}

export function galleryPrimarySource(source: EditSourceState): EditGallerySource | null {
  const found = source.sources.find((item) => item.kind === 'gallery_primary');
  return found?.kind === 'gallery_primary' ? found : null;
}

export function uploadSources(source: EditSourceState): EditUploadSource[] {
  return source.sources.filter((item): item is EditUploadSource => item.kind === 'upload');
}

export function galleryReferenceSources(source: EditSourceState): EditGallerySource[] {
  return source.sources.filter(
    (item): item is EditGallerySource => item.kind === 'gallery_reference'
  );
}

export function editSourceCount(source: EditSourceState) {
  return source.sources.length;
}

/** Matches a source by id, then falls back to a gallery reference's image id. */
export function findEditSource(source: EditSourceState, idOrImageId: string): EditSource | null {
  return (
    source.sources.find((item) => item.id === idOrImageId) ??
    source.sources.find((item) => item.kind === 'gallery_reference' && item.imageId === idOrImageId) ??
    null
  );
}

/**
 * The identity the server can verify when restoring a job's mask or markup:
 * the gallery primary or the first upload. A bare reference has none.
 */
export function primaryRestoreSource(
  source: EditSourceState
): { galleryImageId: string } | { file: File } | null {
  const primary = primaryEditSource(source);
  if (primary?.kind === 'gallery_primary') return { galleryImageId: primary.imageId };
  if (primary?.kind === 'upload') return { file: primary.file };
  return null;
}

export function isMaskValid(source: EditSourceState) {
  return Boolean(source.mask && source.mask.sourceId === primaryEditSourceId(source));
}

export function isImageFile(file: File): boolean {
  if (file.type.startsWith('image/') && file.type !== 'image/svg+xml') return true;
  return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i.test(file.name);
}

function revokeEditSourceUrls(source: EditSourceState) {
  uploadSources(source).forEach((upload) => URL.revokeObjectURL(upload.previewUrl));
}

const SOURCE_KIND_ORDER: Record<EditSource['kind'], number> = {
  gallery_primary: 0,
  upload: 1,
  gallery_reference: 2
};

// Array.prototype.sort is stable, so insertion order survives within a kind.
function normalizeSources(sources: EditSource[]): EditSource[] {
  return [...sources].sort((a, b) => SOURCE_KIND_ORDER[a.kind] - SOURCE_KIND_ORDER[b.kind]);
}

function revokeMaskUrl(mask: EditMask | null) {
  if (mask) URL.revokeObjectURL(mask.previewUrl);
}

// The mask is bound to the primary image: only a change of primary invalidates
// it, so adding or removing a reference image must not discard the user's work.
function dropStaleMask(next: EditSourceState): EditSourceState {
  if (next.markup && next.markup.sourceId !== primaryEditSourceId(next)) {
    URL.revokeObjectURL(next.markup.previewUrl);
    editMarkupDiscards.update((count) => count + 1);
    next = { ...next, markup: null };
  }
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
    kind: 'upload',
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

function createEditSourceStore() {
  const { subscribe, set, update } = writable<EditSourceState>(initialEditSourceState);
  let state = initialEditSourceState;

  subscribe((value) => {
    state = value;
  });

  // The single write path for `sources`: put the result in canonical order and
  // drop the mask/markup if the primary image moved. Returning the same array
  // from `transform` means "nothing changed" and leaves the state untouched.
  function commit(transform: (sources: EditSource[]) => EditSource[]) {
    update((source) => {
      const next = transform(source.sources);
      if (next === source.sources) return source;
      return dropStaleMask({ ...source, sources: normalizeSources(next) });
    });
  }

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
    commit((sources) => [...sources, ...nextUploads]);
    for (const upload of nextUploads) {
      void probeImageSize(upload.file).then((size) => {
        if (size) updateUploadSize(upload.id, size.width, size.height);
      });
    }
    return nextUploads.length;
  }

  function updateUploadSize(sourceId: string, width: number, height: number) {
    commit((sources) =>
      sources.some((item) => item.kind === 'upload' && item.id === sourceId)
        ? sources.map((item) =>
            item.kind === 'upload' && item.id === sourceId ? { ...item, width, height } : item
          )
        : sources
    );
  }

  function dropSources(matches: (item: EditSource) => boolean) {
    commit((sources) =>
      sources.some(matches) ? sources.filter((item) => !matches(item)) : sources
    );
  }

  return {
    subscribe,
    set,
    update,
    addFiles,
    updateUploadSize,
    clear(input?: HTMLInputElement) {
      if (state.markup) URL.revokeObjectURL(state.markup.previewUrl);
      revokeEditSourceUrls(state);
      if (state.mask) editMaskDiscards.update((count) => count + 1);
      revokeMaskUrl(state.mask);
      set({ ...initialEditSourceState, sources: [] });
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
      height = 0,
      filename = ''
    ) {
      const current = state;
      if (!galleryPrimarySource(current) && editSourceCount(current) >= MAX_EDIT_SOURCE_IMAGES) {
        setError?.(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
        return false;
      }

      // Replaces any previous gallery primary rather than adding a second one.
      commit((sources) => [
        ...sources.filter((item) => item.kind !== 'gallery_primary'),
        {
          kind: 'gallery_primary',
          id: imageId,
          imageId,
          filename,
          label,
          previewUrl,
          previewLabel,
          width,
          height
        }
      ]);
      return true;
    },
    // Without an id (or with an empty one) this clears whatever primary is set.
    clearGallerySource(imageId?: string) {
      dropSources((item) => item.kind === 'gallery_primary' && (!imageId || item.id === imageId));
    },
    addGalleryReferences(
      items: EditGalleryReferenceSource[],
      setError?: (message: string) => void
    ) {
      const validItems = items.filter(
        (item) => !/\.(svg|gif)$/i.test(item.filename || item.label || '')
      );
      const invalidCount = items.length - validItems.length;
      if (!validItems.length) {
        if (items.length && setError) setError(get(t).messages.imageUploadRequired);
        return 0;
      }
      const current = state;
      const availableSlots = MAX_EDIT_SOURCE_IMAGES - editSourceCount(current);
      if (availableSlots <= 0) {
        if (setError) setError(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
        return 0;
      }
      const accepted = validItems.slice(0, availableSlots);
      const overLimitCount = validItems.length - accepted.length;
      if (invalidCount > 0 && setError) setError(get(t).messages.imageUploadRequired);
      if (overLimitCount > 0 && setError)
        setError(get(t).messages.editSourceSomeSkipped(MAX_EDIT_SOURCE_IMAGES));

      commit((sources) => [
        ...sources,
        ...accepted.map((item): EditGallerySource => ({ ...item, kind: 'gallery_reference' }))
      ]);
      return accepted.length;
    },
    removeGalleryReference(idOrImageId: string) {
      dropSources(
        (item) =>
          item.kind === 'gallery_reference' &&
          (item.id === idOrImageId || item.imageId === idOrImageId)
      );
    },
    clearGalleryReferences() {
      dropSources((item) => item.kind === 'gallery_reference');
    },
    /** Remove every gallery source (primary and references) matching the deleted ids, or all of them when omitted. */
    removeGallerySourcesByImageIds(imageIds?: string[]) {
      if (imageIds && imageIds.length === 0) return;
      const targets = imageIds ? new Set(imageIds) : null;
      dropSources((item) => item.kind !== 'upload' && (!targets || targets.has(item.imageId)));
    },
    // Accepts a source id, or the image id of a gallery reference.
    remove(sourceId: string) {
      const target = findEditSource(state, sourceId);
      if (!target) return false;
      if (target.kind === 'upload') URL.revokeObjectURL(target.previewUrl);
      dropSources((item) => item === target);
      return true;
    },
    setMarkup(markup: EditMarkup) {
      if (state.markup) URL.revokeObjectURL(state.markup.previewUrl);
      revokeMaskUrl(state.mask);
      update((source) => ({ ...source, mask: null, markup }));
    },
    removeMarkup() {
      if (state.markup) URL.revokeObjectURL(state.markup.previewUrl);
      update((source) => ({ ...source, markup: null }));
    },
    setMask(mask: EditMask) {
      if (state.markup) URL.revokeObjectURL(state.markup.previewUrl);
      revokeMaskUrl(state.mask);
      update((source) => ({ ...source, markup: null, mask }));
    },
    removeMask() {
      revokeMaskUrl(state.mask);
      update((source) => ({ ...source, mask: null }));
    },
    cleanup() {
      if (state.markup) URL.revokeObjectURL(state.markup.previewUrl);
      revokeEditSourceUrls(state);
      revokeMaskUrl(state.mask);
    }
  };
}

export const editSourceStore = createEditSourceStore();
