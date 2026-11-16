import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import {
  editSourceDiscards,
  editSourceStore,
  initialEditSourceState,
  MAX_EDIT_SOURCE_IMAGES,
  primaryEditSourceId,
  submitBlocker,
  type EditGalleryReferenceSource,
  type EditMask,
  type EditMarkup,
  type EditSourceDiscardNotice
} from '$lib/stores/editSource';
import { previewStore } from '$lib/stores/preview';
import { initialPromptFormState } from '$lib/utils/promptFormTypes';

vi.mock('$lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('$lib/api/client')>()),
  apiFetch: vi.fn()
}));

const apiFetchMock = vi.mocked(apiFetch);

const makeFile = (name: string) => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });

const makeMask = (sourceId: string, previewUrl = 'blob:mask'): EditMask => ({
  sourceId,
  blob: new Blob([new Uint8Array([1])], { type: 'image/png' }),
  width: 8,
  height: 8,
  coverage: 0.25,
  previewUrl,
  origin: 'painted'
});

const makeMarkup = (sourceId: string, previewUrl = 'blob:markup'): EditMarkup => ({
  sourceId,
  blob: new Blob([new Uint8Array([1])], { type: 'image/png' }),
  width: 8,
  height: 8,
  previewUrl,
  origin: 'painted'
});

const makeRef = (id: string): EditGalleryReferenceSource => ({
  id: `ref-${id}`,
  imageId: id,
  filename: `${id}.png`,
  label: id,
  previewUrl: `blob:${id}`,
  previewLabel: id,
  width: 10,
  height: 10
});

let revokeSpy = vi.fn<(url: string) => void>();

beforeEach(() => {
  revokeSpy = vi.fn<(url: string) => void>();
  let counter = 0;
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => `blob:char-${(counter += 1)}`;
  (URL as unknown as { revokeObjectURL: (url: string) => void }).revokeObjectURL = revokeSpy;
  editSourceStore.reset();
  apiFetchMock.mockReset();
});

describe('edit-source session characterization (C3)', () => {
  it('emits mask discard notices on primary change, gallery takeover, and clear()', () => {
    const notices: EditSourceDiscardNotice[] = [];
    const unsubscribe = editSourceDiscards.onDiscard((notice) => notices.push(notice));
    try {
      editSourceStore.addFiles([makeFile('a.png')], () => {});
      const uploadId = primaryEditSourceId(get(editSourceStore));
      editSourceStore.setMask(makeMask(uploadId, 'blob:mask-1'));

      // Gallery primary overtakes upload primary -> mask discarded
      editSourceStore.setGallerySource('gal-1', 'Gal 1', 'blob:gal-1', 'gal-1');
      expect(get(editSourceStore).mask).toBeNull();
      expect(revokeSpy).toHaveBeenCalledWith('blob:mask-1');

      // Clear with active mask -> cleared notice emitted
      editSourceStore.setMask(makeMask('gal-1', 'blob:mask-2'));
      editSourceStore.clear();
      expect(get(editSourceStore).mask).toBeNull();
      expect(revokeSpy).toHaveBeenCalledWith('blob:mask-2');

      expect(notices).toEqual([
        { seq: 1, kind: 'mask', reason: 'primary-changed' },
        { seq: 2, kind: 'mask', reason: 'cleared' }
      ]);
      expect(get(editSourceDiscards)).toEqual({ seq: 2, kind: 'mask', reason: 'cleared' });
    } finally {
      unsubscribe();
    }
  });

  it('emits markup discard notice on primary change, but not on clear()', () => {
    const notices: EditSourceDiscardNotice[] = [];
    const unsubscribe = editSourceDiscards.onDiscard((notice) => notices.push(notice));
    try {
      editSourceStore.addFiles([makeFile('a.png')], () => {});
      const uploadId = primaryEditSourceId(get(editSourceStore));
      editSourceStore.setMarkup(makeMarkup(uploadId, 'blob:markup-1'));

      // Gallery primary overtakes upload primary -> markup discarded
      editSourceStore.setGallerySource('gal-1', 'Gal 1', 'blob:gal-1', 'gal-1');
      expect(get(editSourceStore).markup).toBeNull();
      expect(revokeSpy).toHaveBeenCalledWith('blob:markup-1');

      // Clear with active markup -> revokes URL, does NOT emit discard notice
      editSourceStore.setMarkup(makeMarkup('gal-1', 'blob:markup-2'));
      editSourceStore.clear();
      expect(get(editSourceStore).markup).toBeNull();
      expect(revokeSpy).toHaveBeenCalledWith('blob:markup-2');

      expect(notices).toEqual([{ seq: 1, kind: 'markup', reason: 'primary-changed' }]);
    } finally {
      unsubscribe();
    }
  });

  it('preserves the setMarkup/setMask discard asymmetry', () => {
    const notices: EditSourceDiscardNotice[] = [];
    const unsubscribe = editSourceDiscards.onDiscard((notice) => notices.push(notice));
    try {
      editSourceStore.addFiles([makeFile('a.png')], () => {});
      const primaryId = primaryEditSourceId(get(editSourceStore));

      // setMarkup over an active mask emits { kind: 'mask', reason: 'replaced' }
      editSourceStore.setMask(makeMask(primaryId, 'blob:mask-a'));
      editSourceStore.setMarkup(makeMarkup(primaryId, 'blob:markup-a'));
      expect(get(editSourceStore).mask).toBeNull();
      expect(get(editSourceStore).markup?.previewUrl).toBe('blob:markup-a');
      expect(revokeSpy).toHaveBeenCalledWith('blob:mask-a');

      // setMask over an active markup silently clears markup without emitting a discard notice
      editSourceStore.setMask(makeMask(primaryId, 'blob:mask-b'));
      expect(get(editSourceStore).markup).toBeNull();
      expect(get(editSourceStore).mask?.previewUrl).toBe('blob:mask-b');
      expect(revokeSpy).toHaveBeenCalledWith('blob:markup-a');

      expect(notices).toEqual([{ seq: 1, kind: 'mask', reason: 'replaced' }]);
    } finally {
      unsubscribe();
    }
  });

  it('does not emit discard notices for explicit removeMask/removeMarkup or same-kind replacement', () => {
    const notices: EditSourceDiscardNotice[] = [];
    const unsubscribe = editSourceDiscards.onDiscard((notice) => notices.push(notice));
    try {
      editSourceStore.addFiles([makeFile('a.png')], () => {});
      const primaryId = primaryEditSourceId(get(editSourceStore));

      editSourceStore.setMask(makeMask(primaryId, 'blob:m1'));
      editSourceStore.setMask(makeMask(primaryId, 'blob:m2'));
      editSourceStore.removeMask();

      editSourceStore.setMarkup(makeMarkup(primaryId, 'blob:k1'));
      editSourceStore.setMarkup(makeMarkup(primaryId, 'blob:k2'));
      editSourceStore.removeMarkup();

      expect(notices).toEqual([]);
      expect(get(editSourceDiscards)).toBeNull();
    } finally {
      unsubscribe();
    }
  });

  it('validates edit-source submit blockers with exact localized messages', async () => {
    const messages = get(t);
    const submitWithCurrent = () =>
      previewStore.editImage({ ...initialPromptFormState, prompt: 'edit me' }, get(editSourceStore), {
        trackJob: () => {}
      });

    // Empty sources
    expect(await submitWithCurrent()).toBe(false);
    expect(get(previewStore).error).toBe(messages.messages.editSourceRequired);

    // Too many sources (> MAX_EDIT_SOURCE_IMAGES)
    editSourceStore.reset({
      ...initialEditSourceState,
      sources: Array.from({ length: MAX_EDIT_SOURCE_IMAGES + 1 }, (_, idx) => ({
        kind: 'upload' as const,
        id: `up-${idx}`,
        file: makeFile(`f-${idx}.png`),
        label: `f-${idx}.png`,
        previewUrl: `blob:up-${idx}`,
        previewLabel: `f-${idx}.png`,
        width: 8,
        height: 8
      }))
    });
    expect(await submitWithCurrent()).toBe(false);
    expect(get(previewStore).error).toBe(messages.messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));

    // Markup on a bare gallery_reference primary
    editSourceStore.reset({
      ...initialEditSourceState,
      sources: [
        {
          kind: 'gallery_reference',
          id: 'ref-only',
          imageId: 'img-only',
          label: 'Ref',
          previewUrl: 'blob:ref',
          previewLabel: 'Ref',
          width: 8,
          height: 8
        }
      ],
      markup: makeMarkup('ref-only')
    });
    expect(await submitWithCurrent()).toBe(false);
    expect(get(previewStore).error).toBe(messages.promptForm.invalidMarkup);

    // Stale mask
    editSourceStore.reset();
    editSourceStore.addGalleryReferences([makeRef('r1')]);
    editSourceStore.setMask(makeMask('wrong-id'));
    expect(await submitWithCurrent()).toBe(false);
    expect(get(previewStore).error).toBe(messages.messages.editMaskStale);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('submitBlocker returns exact blocker keys for every invalid state and null when valid', () => {
    expect(submitBlocker(initialEditSourceState)).toBe('no_sources');

    const validUpload = {
      kind: 'upload' as const,
      id: 'up-1',
      file: makeFile('a.png'),
      label: 'a.png',
      previewUrl: 'blob:up-1',
      previewLabel: 'a.png',
      width: 8,
      height: 8
    };
    expect(submitBlocker({ sources: [validUpload], mask: null, markup: null })).toBeNull();
    expect(
      submitBlocker({
        sources: Array.from({ length: MAX_EDIT_SOURCE_IMAGES + 1 }, (_, i) => ({
          ...validUpload,
          id: `up-${i}`
        })),
        mask: null,
        markup: null
      })
    ).toBe('too_many_sources');
    expect(
      submitBlocker({
        sources: [validUpload],
        mask: makeMask('up-1'),
        markup: makeMarkup('up-1')
      })
    ).toBe('invalid_markup');
    expect(
      submitBlocker({
        sources: [validUpload],
        mask: makeMask('other-id'),
        markup: null
      })
    ).toBe('stale_mask');
  });
});
