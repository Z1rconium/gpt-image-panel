import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  editMarkupDiscards,
  editMaskDiscards,
  editSourceCount,
  editSourceStore,
  findEditSource,
  galleryPrimarySource,
  galleryReferenceSources,
  initialEditSourceState,
  isMaskValid,
  primaryEditSourceId,
  primaryRestoreSource,
  uploadSources,
  type EditMask
} from '$lib/stores/editSource';

function makeFile(name: string) {
  return new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });
}

function makeMask(sourceId: string, previewUrl: string): EditMask {
  return {
    sourceId,
    blob: new Blob([new Uint8Array([1])], { type: 'image/png' }),
    width: 8,
    height: 8,
    coverage: 0.5,
    previewUrl,
    origin: 'painted'
  };
}

const uploads = () => uploadSources(get(editSourceStore));
const galleryRefs = () => galleryReferenceSources(get(editSourceStore));
const galleryPrimary = () => galleryPrimarySource(get(editSourceStore));
const ids = () => get(editSourceStore).sources.map((item) => item.id);

let revokeSpy = vi.fn<(url: string) => void>();

beforeEach(() => {
  revokeSpy = vi.fn<(url: string) => void>();
  let counter = 0;
  // The node test environment has no object URLs; the store creates and
  // revokes them on every mutation, so both need stubbing.
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () =>
    `blob:test-${(counter += 1)}`;
  (URL as unknown as { revokeObjectURL: (url: string) => void }).revokeObjectURL = revokeSpy;
  editSourceStore.set({ ...initialEditSourceState });
  editMaskDiscards.set(0);
  editMarkupDiscards.set(0);
});

describe('edit source primary selection', () => {
  it('uses the first upload until a gallery image takes over', () => {
    expect(primaryEditSourceId(get(editSourceStore))).toBe('');
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const uploaded = uploads()[0].id;
    expect(primaryEditSourceId(get(editSourceStore))).toBe(uploaded);

    editSourceStore.setGallerySource('gallery-1', 'Gallery', 'blob:gallery', 'gallery');
    expect(primaryEditSourceId(get(editSourceStore))).toBe('gallery-1');
    expect(editSourceCount(get(editSourceStore))).toBe(2);
  });

  it('treats a mask as valid only for the primary source', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = uploads()[0].id;
    editSourceStore.setMask(makeMask(primaryId, 'blob:mask'));
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    editSourceStore.setMask(makeMask('someone-else', 'blob:mask-other'));
    expect(isMaskValid(get(editSourceStore))).toBe(false);
  });
});

describe('mask binding', () => {
  it('keeps the mask when a reference image is added or removed', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = uploads()[0].id;
    editSourceStore.setMask(makeMask(primaryId, 'blob:mask'));

    editSourceStore.addFiles([makeFile('b.png')], () => {});
    expect(uploads()).toHaveLength(2);
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    const referenceId = uploads()[1].id;
    editSourceStore.remove(referenceId);
    expect(isMaskValid(get(editSourceStore))).toBe(true);
    expect(get(editSourceStore).mask?.sourceId).toBe(primaryId);
    expect(get(editMaskDiscards)).toBe(0);
  });

  it('drops the mask and counts a discard when the primary upload is removed', () => {
    editSourceStore.addFiles([makeFile('a.png'), makeFile('b.png')], () => {});
    const [primary, reference] = uploads();
    editSourceStore.setMask(makeMask(primary.id, 'blob:mask'));

    editSourceStore.remove(reference.id);
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    editSourceStore.remove(primary.id);
    expect(get(editSourceStore).mask).toBeNull();
    expect(get(editMaskDiscards)).toBe(1);
  });

  it('drops the mask when the gallery primary is cleared', () => {
    editSourceStore.setGallerySource('gallery-1', 'Gallery', 'blob:gallery', 'gallery');
    editSourceStore.setMask(makeMask('gallery-1', 'blob:mask'));
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    editSourceStore.clearGallerySource('gallery-1');
    expect(get(editSourceStore).mask).toBeNull();
    expect(get(editMaskDiscards)).toBe(1);
  });

  it('counts a discard when every source is cleared', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = uploads()[0].id;
    editSourceStore.setMask(makeMask(primaryId, 'blob:mask'));

    editSourceStore.clear();
    expect(editSourceCount(get(editSourceStore))).toBe(0);
    expect(get(editSourceStore).mask).toBeNull();
    expect(get(editMaskDiscards)).toBe(1);
  });
});

describe('edit source sizes (F6)', () => {
  it('records a gallery primary size and clears it when the gallery source is dropped', () => {
    editSourceStore.setGallerySource('gallery-1', 'Gallery', 'blob:gallery', 'gallery', undefined, 64, 32);
    expect(galleryPrimary()).toMatchObject({ width: 64, height: 32 });

    editSourceStore.clearGallerySource('gallery-1');
    expect(galleryPrimary()).toBeNull();
    expect(editSourceCount(get(editSourceStore))).toBe(0);
  });

  it('defaults an unspecified gallery size to 0 (unknown)', () => {
    editSourceStore.setGallerySource('gallery-1', 'Gallery', 'blob:gallery', 'gallery');
    expect(galleryPrimary()).toMatchObject({ width: 0, height: 0 });
  });

  it('starts an upload at width/height 0 and patches it via updateUploadSize', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const uploadId = uploads()[0].id;
    expect(uploads()[0].width).toBe(0);
    expect(uploads()[0].height).toBe(0);

    editSourceStore.updateUploadSize(uploadId, 128, 96);
    expect(uploads()[0].width).toBe(128);
    expect(uploads()[0].height).toBe(96);
  });
});

describe('mask lifecycle', () => {
  it('revokes the previous mask preview when replaced or removed', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = uploads()[0].id;

    editSourceStore.setMask(makeMask(primaryId, 'blob:first'));
    editSourceStore.setMask(makeMask(primaryId, 'blob:second'));
    expect(revokeSpy).toHaveBeenCalledWith('blob:first');
    expect(get(editSourceStore).mask?.previewUrl).toBe('blob:second');

    editSourceStore.removeMask();
    expect(get(editSourceStore).mask).toBeNull();
    expect(revokeSpy).toHaveBeenCalledWith('blob:second');
  });
});

describe('markup primary binding', () => {
  it('preserves markup for reference changes and revokes it when the primary changes', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const id = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMarkup({ sourceId: id, blob: makeFile('markup.png'), width: 8, height: 8, previewUrl: 'blob:markup' });
    editSourceStore.addFiles([makeFile('reference.png')], () => {});
    expect(get(editSourceStore).markup?.sourceId).toBe(id);
    editSourceStore.remove(id);
    expect(get(editSourceStore).markup).toBeNull();
    expect(revokeSpy).toHaveBeenCalledWith('blob:markup');
  });
  it('makes mask and markup mutually exclusive', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const id = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMask(makeMask(id, 'blob:mask'));
    editSourceStore.setMarkup({ sourceId: id, blob: makeFile('markup.png'), width: 8, height: 8, previewUrl: 'blob:markup' });
    expect(get(editSourceStore).mask).toBeNull();
    editSourceStore.setMask(makeMask(id, 'blob:newmask'));
    expect(get(editSourceStore).markup).toBeNull();
  });
});

describe('markup editor state (browser-only, optional)', () => {
  const state = (commands = 1) => ({
    width: 8,
    height: 8,
    base: null,
    commands: Object.freeze(Array.from({ length: commands }, (_, index) => Object.freeze({ id: index + 1, kind: 'clear' as const })))
  });

  it('still accepts the legacy markup shape without origin or editor state', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const id = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMarkup({ sourceId: id, blob: makeFile('markup.png'), width: 8, height: 8, previewUrl: 'blob:legacy' });
    const markup = get(editSourceStore).markup;
    expect(markup?.origin).toBeUndefined();
    expect(markup?.editorState).toBeUndefined();
  });

  it('carries origin and the immutable editor state next to the PNG', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const id = primaryEditSourceId(get(editSourceStore));
    const editorState = state();
    editSourceStore.setMarkup({
      sourceId: id,
      blob: makeFile('markup.png'),
      width: 8,
      height: 8,
      previewUrl: 'blob:painted',
      origin: 'painted',
      editorState
    });
    expect(get(editSourceStore).markup?.origin).toBe('painted');
    expect(get(editSourceStore).markup?.editorState).toBe(editorState);
    expect(Object.isFrozen(get(editSourceStore).markup?.editorState?.commands)).toBe(true);
  });

  it('replacing a markup releases the previous preview exactly once', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const id = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMarkup({ sourceId: id, blob: makeFile('a.png'), width: 8, height: 8, previewUrl: 'blob:first', origin: 'painted', editorState: state() });
    editSourceStore.setMarkup({ sourceId: id, blob: makeFile('b.png'), width: 8, height: 8, previewUrl: 'blob:second', origin: 'painted', editorState: state(2) });
    expect(revokeSpy.mock.calls.filter(([url]) => url === 'blob:first')).toHaveLength(1);
    expect(revokeSpy.mock.calls.filter(([url]) => url === 'blob:second')).toHaveLength(0);
    expect(get(editSourceStore).markup?.editorState?.commands).toHaveLength(2);
  });

  it('counts a discard only when a primary change drops the markup, not for reference changes', () => {
    editSourceStore.addFiles([makeFile('primary.png'), makeFile('reference.png')], () => {});
    const [primary, reference] = uploads();
    editSourceStore.setMarkup({ sourceId: primary.id, blob: makeFile('markup.png'), width: 8, height: 8, previewUrl: 'blob:markup', origin: 'painted', editorState: state() });
    editSourceStore.remove(reference.id);
    editSourceStore.addFiles([makeFile('another.png')], () => {});
    expect(get(editSourceStore).markup?.sourceId).toBe(primary.id);
    expect(get(editMarkupDiscards)).toBe(0);
    editSourceStore.remove(primary.id);
    expect(get(editSourceStore).markup).toBeNull();
    expect(get(editMarkupDiscards)).toBe(1);
  });

  it('removeMarkup revokes the preview and drops the editor state with it', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const id = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMarkup({ sourceId: id, blob: makeFile('markup.png'), width: 8, height: 8, previewUrl: 'blob:gone', origin: 'painted', editorState: state() });
    editSourceStore.removeMarkup();
    expect(get(editSourceStore).markup).toBeNull();
    expect(revokeSpy).toHaveBeenCalledWith('blob:gone');
  });
});

describe('gallery references (Phase 2)', () => {
  it('adds and removes gallery references while updating source count', () => {
    expect(editSourceCount(get(editSourceStore))).toBe(0);
    editSourceStore.addGalleryReferences([
      {
        id: 'ref-1',
        imageId: 'img-1',
        label: 'Ref 1',
        previewUrl: 'blob:ref-1',
        previewLabel: 'Ref 1',
        width: 100,
        height: 100
      },
      {
        id: 'ref-2',
        imageId: 'img-2',
        label: 'Ref 2',
        previewUrl: 'blob:ref-2',
        previewLabel: 'Ref 2',
        width: 100,
        height: 100
      }
    ]);
    expect(editSourceCount(get(editSourceStore))).toBe(2);
    expect(galleryRefs()).toHaveLength(2);

    // Remove by imageId or id
    editSourceStore.remove('ref-1');
    expect(editSourceCount(get(editSourceStore))).toBe(1);
    expect(galleryRefs()[0].id).toBe('ref-2');

    editSourceStore.remove('img-2');
    expect(editSourceCount(get(editSourceStore))).toBe(0);
  });

  it('preserves existing mask when adding or removing gallery references', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const primaryId = uploads()[0].id;
    editSourceStore.setMask(makeMask(primaryId, 'blob:mask'));
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    // Adding gallery references must NOT discard the mask
    editSourceStore.addGalleryReferences([
      {
        id: 'ref-1',
        imageId: 'img-1',
        label: 'Ref 1',
        previewUrl: 'blob:ref-1',
        previewLabel: 'Ref 1',
        width: 200,
        height: 200
      }
    ]);
    expect(isMaskValid(get(editSourceStore))).toBe(true);
    expect(get(editMaskDiscards)).toBe(0);

    // Removing gallery reference must NOT discard the mask
    editSourceStore.remove('ref-1');
    expect(isMaskValid(get(editSourceStore))).toBe(true);
    expect(get(editMaskDiscards)).toBe(0);
  });

  it('filters out SVG and GIF files when adding gallery references', () => {
    const errorSpy = vi.fn();
    const count = editSourceStore.addGalleryReferences(
      [
        {
          id: 'ref-svg',
          imageId: 'img-svg',
          filename: 'icon.svg',
          label: 'SVG',
          previewUrl: 'blob:svg',
          previewLabel: 'SVG',
          width: 0,
          height: 0
        },
        {
          id: 'ref-gif',
          imageId: 'img-gif',
          filename: 'anim.gif',
          label: 'GIF',
          previewUrl: 'blob:gif',
          previewLabel: 'GIF',
          width: 0,
          height: 0
        },
        {
          id: 'ref-valid',
          imageId: 'img-valid',
          filename: 'valid.png',
          label: 'Valid',
          previewUrl: 'blob:valid',
          previewLabel: 'Valid',
          width: 50,
          height: 50
        }
      ],
      errorSpy
    );
    expect(count).toBe(1);
    expect(galleryRefs()).toHaveLength(1);
    expect(galleryRefs()[0].id).toBe('ref-valid');
    expect(errorSpy).toHaveBeenCalled();
  });

  it('enforces total capacity limit of 16 across uploads, gallery primary and references', () => {
    // Add 1 primary gallery and 10 uploads
    editSourceStore.setGallerySource('gal-primary', 'Gal', 'blob:gal', 'gal');
    editSourceStore.addFiles(
      Array.from({ length: 10 }, (_, i) => makeFile(`up-${i}.png`)),
      () => {}
    );
    expect(editSourceCount(get(editSourceStore))).toBe(11);

    // Try adding 7 references (total would be 18 > 16)
    const errorSpy = vi.fn();
    const added = editSourceStore.addGalleryReferences(
      Array.from({ length: 7 }, (_, i) => ({
        id: `ref-${i}`,
        imageId: `img-${i}`,
        label: `Ref ${i}`,
        previewUrl: `blob:ref-${i}`,
        previewLabel: `Ref ${i}`,
        width: 10,
        height: 10
      })),
      errorSpy
    );
    // Only 5 should be accepted (11 + 5 = 16)
    expect(added).toBe(5);
    expect(editSourceCount(get(editSourceStore))).toBe(16);
    expect(errorSpy).toHaveBeenCalled();
  });

  it('clear() empties gallery references as well', () => {
    editSourceStore.addGalleryReferences([
      {
        id: 'ref-1',
        imageId: 'img-1',
        label: 'Ref 1',
        previewUrl: 'blob:ref-1',
        previewLabel: 'Ref 1',
        width: 10,
        height: 10
      }
    ]);
    expect(editSourceCount(get(editSourceStore))).toBe(1);
    editSourceStore.clear();
    expect(editSourceCount(get(editSourceStore))).toBe(0);
    expect(get(editSourceStore).sources).toEqual([]);
  });
});

describe('unified sources array', () => {
  const ref = (id: string, imageId = `img-${id}`) => ({
    id,
    imageId,
    filename: `${id}.png`,
    label: id,
    previewUrl: `blob:${id}`,
    previewLabel: id,
    width: 10,
    height: 10
  });

  it('keeps sources in canonical order whatever order they were added in', () => {
    editSourceStore.addGalleryReferences([ref('ref-1')]);
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    editSourceStore.addGalleryReferences([ref('ref-2')]);
    editSourceStore.setGallerySource('gal', 'Gal', 'blob:gal', 'gal');
    editSourceStore.addFiles([makeFile('b.png')], () => {});

    const { sources } = get(editSourceStore);
    expect(sources.map((item) => item.kind)).toEqual([
      'gallery_primary',
      'upload',
      'upload',
      'gallery_reference',
      'gallery_reference'
    ]);
    expect(sources.filter((item) => item.kind === 'upload').map((item) => (item as { file: File }).file.name)).toEqual(['a.png', 'b.png']);
    expect(sources.slice(3).map((item) => item.id)).toEqual(['ref-1', 'ref-2']);
    expect(primaryEditSourceId(get(editSourceStore))).toBe('gal');
  });

  it('falls back to the first upload, then the first reference, as the primary', () => {
    editSourceStore.addGalleryReferences([ref('ref-1')]);
    expect(primaryEditSourceId(get(editSourceStore))).toBe('ref-1');
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    expect(primaryEditSourceId(get(editSourceStore))).toBe(uploads()[0].id);
  });

  it('moves the primary (and drops the mask) when an upload overtakes a reference-only selection', () => {
    editSourceStore.addGalleryReferences([ref('ref-1')]);
    editSourceStore.setMask(makeMask('ref-1', 'blob:mask'));
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    expect(get(editSourceStore).mask).toBeNull();
    expect(get(editMaskDiscards)).toBe(1);
  });

  it('replaces the gallery primary instead of adding a second one', () => {
    editSourceStore.setGallerySource('gal-1', 'One', 'blob:1', 'one');
    editSourceStore.setGallerySource('gal-2', 'Two', 'blob:2', 'two');
    expect(get(editSourceStore).sources).toHaveLength(1);
    expect(galleryPrimary()?.id).toBe('gal-2');
    expect(galleryPrimary()?.imageId).toBe('gal-2');
  });

  it('replacing the gallery primary when full is allowed, adding a new one is not', () => {
    editSourceStore.setGallerySource('gal-1', 'One', 'blob:1', 'one');
    editSourceStore.addFiles(Array.from({ length: 15 }, (_, i) => makeFile(`u-${i}.png`)), () => {});
    expect(editSourceCount(get(editSourceStore))).toBe(16);
    expect(editSourceStore.setGallerySource('gal-2', 'Two', 'blob:2', 'two')).toBe(true);
    expect(galleryPrimary()?.id).toBe('gal-2');

    editSourceStore.clearGallerySource();
    editSourceStore.addFiles([makeFile('last.png')], () => {});
    const errorSpy = vi.fn();
    expect(editSourceStore.setGallerySource('gal-3', 'Three', 'blob:3', 'three', errorSpy)).toBe(false);
    expect(errorSpy).toHaveBeenCalled();
    expect(galleryPrimary()).toBeNull();
  });

  it('clearGallerySource ignores a different id but clears the primary when no id is given', () => {
    editSourceStore.setGallerySource('gal-1', 'One', 'blob:1', 'one');
    editSourceStore.clearGallerySource('someone-else');
    expect(galleryPrimary()?.id).toBe('gal-1');
    editSourceStore.clearGallerySource();
    expect(galleryPrimary()).toBeNull();
  });

  it('clearGallerySource leaves references and uploads alone', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    editSourceStore.addGalleryReferences([ref('ref-1')]);
    editSourceStore.setGallerySource('gal-1', 'One', 'blob:1', 'one');
    editSourceStore.clearGallerySource('gal-1');
    expect(get(editSourceStore).sources.map((item) => item.kind)).toEqual(['upload', 'gallery_reference']);
  });

  it('removes a gallery reference by image id, but only revokes object URLs for uploads', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const uploadUrl = uploads()[0].previewUrl;
    editSourceStore.addGalleryReferences([ref('ref-1', 'img-shared')]);
    revokeSpy.mockClear();

    expect(editSourceStore.remove('img-shared')).toBe(true);
    expect(revokeSpy).not.toHaveBeenCalled();
    expect(editSourceStore.remove('unknown')).toBe(false);
    expect(editSourceStore.remove(uploads()[0].id)).toBe(true);
    expect(revokeSpy).toHaveBeenCalledWith(uploadUrl);
    expect(ids()).toEqual([]);
  });

  it('findEditSource prefers an exact id over a reference image id', () => {
    editSourceStore.setGallerySource('img-shared', 'Primary', 'blob:p', 'p');
    editSourceStore.addGalleryReferences([ref('ref-1', 'img-shared')]);
    expect(findEditSource(get(editSourceStore), 'img-shared')?.kind).toBe('gallery_primary');
    expect(findEditSource(get(editSourceStore), 'ref-1')?.kind).toBe('gallery_reference');
    expect(findEditSource(get(editSourceStore), 'nope')).toBeNull();
  });

  it('exposes a restorable identity only for a gallery primary or an upload primary', () => {
    expect(primaryRestoreSource(get(editSourceStore))).toBeNull();
    editSourceStore.addGalleryReferences([ref('ref-1')]);
    expect(primaryRestoreSource(get(editSourceStore))).toBeNull();
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    expect(primaryRestoreSource(get(editSourceStore))).toEqual({ file: uploads()[0].file });
    editSourceStore.setGallerySource('gal-1', 'One', 'blob:1', 'one');
    expect(primaryRestoreSource(get(editSourceStore))).toEqual({ galleryImageId: 'gal-1' });
  });

  it('records the gallery filename so the original can be read without a lookup', () => {
    editSourceStore.setGallerySource('gal-1', 'One', 'blob:1', 'one', undefined, 8, 6, 'one.jpg');
    expect(galleryPrimary()).toMatchObject({ filename: 'one.jpg', width: 8, height: 6 });
  });
});
