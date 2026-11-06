import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  editMarkupDiscards,
  editMaskDiscards,
  editSourceCount,
  editSourceStore,
  initialEditSourceState,
  isMaskValid,
  primaryEditSourceId,
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
    const uploaded = get(editSourceStore).files[0].id;
    expect(primaryEditSourceId(get(editSourceStore))).toBe(uploaded);

    editSourceStore.setGallerySource('gallery-1', 'Gallery', 'blob:gallery', 'gallery');
    expect(primaryEditSourceId(get(editSourceStore))).toBe('gallery-1');
    expect(editSourceCount(get(editSourceStore))).toBe(2);
  });

  it('treats a mask as valid only for the primary source', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = get(editSourceStore).files[0].id;
    editSourceStore.setMask(makeMask(primaryId, 'blob:mask'));
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    editSourceStore.setMask(makeMask('someone-else', 'blob:mask-other'));
    expect(isMaskValid(get(editSourceStore))).toBe(false);
  });
});

describe('mask binding', () => {
  it('keeps the mask when a reference image is added or removed', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = get(editSourceStore).files[0].id;
    editSourceStore.setMask(makeMask(primaryId, 'blob:mask'));

    editSourceStore.addFiles([makeFile('b.png')], () => {});
    expect(get(editSourceStore).files).toHaveLength(2);
    expect(isMaskValid(get(editSourceStore))).toBe(true);

    const referenceId = get(editSourceStore).files[1].id;
    editSourceStore.remove(referenceId);
    expect(isMaskValid(get(editSourceStore))).toBe(true);
    expect(get(editSourceStore).mask?.sourceId).toBe(primaryId);
    expect(get(editMaskDiscards)).toBe(0);
  });

  it('drops the mask and counts a discard when the primary upload is removed', () => {
    editSourceStore.addFiles([makeFile('a.png'), makeFile('b.png')], () => {});
    const [primary, reference] = get(editSourceStore).files;
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
    const primaryId = get(editSourceStore).files[0].id;
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
    expect(get(editSourceStore).galleryWidth).toBe(64);
    expect(get(editSourceStore).galleryHeight).toBe(32);

    editSourceStore.clearGallerySource('gallery-1');
    expect(get(editSourceStore).galleryWidth).toBe(0);
    expect(get(editSourceStore).galleryHeight).toBe(0);
  });

  it('defaults an unspecified gallery size to 0 (unknown)', () => {
    editSourceStore.setGallerySource('gallery-1', 'Gallery', 'blob:gallery', 'gallery');
    expect(get(editSourceStore).galleryWidth).toBe(0);
    expect(get(editSourceStore).galleryHeight).toBe(0);
  });

  it('starts an upload at width/height 0 and patches it via updateUploadSize', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const uploadId = get(editSourceStore).files[0].id;
    expect(get(editSourceStore).files[0].width).toBe(0);
    expect(get(editSourceStore).files[0].height).toBe(0);

    editSourceStore.updateUploadSize(uploadId, 128, 96);
    expect(get(editSourceStore).files[0].width).toBe(128);
    expect(get(editSourceStore).files[0].height).toBe(96);
  });
});

describe('mask lifecycle', () => {
  it('revokes the previous mask preview when replaced or removed', () => {
    editSourceStore.addFiles([makeFile('a.png')], () => {});
    const primaryId = get(editSourceStore).files[0].id;

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
    const [primary, reference] = get(editSourceStore).files;
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
    expect(get(editSourceStore).galleryReferences).toHaveLength(2);

    // Remove by imageId or id
    editSourceStore.remove('ref-1');
    expect(editSourceCount(get(editSourceStore))).toBe(1);
    expect(get(editSourceStore).galleryReferences[0].id).toBe('ref-2');

    editSourceStore.remove('img-2');
    expect(editSourceCount(get(editSourceStore))).toBe(0);
  });

  it('preserves existing mask when adding or removing gallery references', () => {
    editSourceStore.addFiles([makeFile('primary.png')], () => {});
    const primaryId = get(editSourceStore).files[0].id;
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
    expect(get(editSourceStore).galleryReferences).toHaveLength(1);
    expect(get(editSourceStore).galleryReferences[0].id).toBe('ref-valid');
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

  it('clear() empties galleryReferences as well', () => {
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
    expect(get(editSourceStore).galleryReferences).toEqual([]);
  });
});
