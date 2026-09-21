import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
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

let revokeSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  revokeSpy = vi.fn();
  let counter = 0;
  // The node test environment has no object URLs; the store creates and
  // revokes them on every mutation, so both need stubbing.
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () =>
    `blob:test-${(counter += 1)}`;
  (URL as unknown as { revokeObjectURL: (url: string) => void }).revokeObjectURL = revokeSpy;
  editSourceStore.set({ ...initialEditSourceState });
  editMaskDiscards.set(0);
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
