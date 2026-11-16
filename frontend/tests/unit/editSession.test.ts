import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { t } from '$lib/i18n';
import {
  editSourceDiscards,
  editSourceStore,
  primaryEditSourceId,
  type EditMask,
  type EditSourceDiscardNotice
} from '$lib/stores/editSource';
import { createEditSession, type MaskImageHelpers } from '$lib/features/workspace/editSession';
import { MarkupRejectedError, type MarkupApplyPayload } from '$lib/features/workspace/markup';

function pngBlob(width: number, height: number): Blob {
  const bytes = new Uint8Array(33);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10], 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([73, 72, 68, 82], 12);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return new Blob([bytes], { type: 'image/png' });
}

function makeJob(overrides: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return {
    job_id: 'job-123',
    operation: 'edit',
    status: 'success',
    stage: 'done',
    prompt: 'edit prompt',
    model: 'gpt-image-1.5',
    size: '1024x1024',
    quality: 'auto',
    n: 1,
    created_at: '2026-11-16T00:00:00Z',
    updated_at: '2026-11-16T00:00:00Z',
    images: [],
    mask_applied: true,
    ...overrides
  } as GenerateJobStatus;
}

function makeMask(sourceId: string, previewUrl = 'blob:mask-new'): EditMask {
  return {
    sourceId,
    blob: new Blob([new Uint8Array([1, 2])], { type: 'image/png' }),
    width: 16,
    height: 16,
    coverage: 0.42,
    previewUrl,
    origin: 'painted'
  };
}

let revokeSpy: ReturnType<typeof vi.fn<(url: string) => void>>;

beforeEach(() => {
  revokeSpy = vi.fn<(url: string) => void>();
  let counter = 0;
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => `blob:session-${(counter += 1)}`;
  (URL as unknown as { revokeObjectURL: (url: string) => void }).revokeObjectURL = revokeSpy;
  editSourceStore.reset();
});

describe('editSession mask restore (C3-S4)', () => {
  it('restores via the PNG-header fast path when X-Mask-Coverage is present without decoding', async () => {
    editSourceStore.setGallerySource('gal-1', 'Gal 1', 'blob:gal-1', 'gal-1', undefined, 320, 240);
    const fetchRaw = vi.fn(async () =>
      new Response(pngBlob(320, 240), {
        status: 200,
        headers: { 'X-Mask-Coverage': '0.25' }
      })
    );
    const readPngSize = vi.fn(() => ({ width: 320, height: 240 }));
    const measureMaskBlob = vi.fn(async () => ({ width: 0, height: 0, coverage: 0 }));
    const toast = vi.fn();

    const session = createEditSession({
      fetchRaw,
      loadMaskHelpers: async (): Promise<MaskImageHelpers> => ({ readPngSize, measureMaskBlob }),
      toast
    });

    const outcome = await session.restoreMaskFromJob(makeJob());
    expect(outcome).toBe('restored');
    expect(readPngSize).toHaveBeenCalledTimes(1);
    expect(measureMaskBlob).not.toHaveBeenCalled();

    const mask = get(editSourceStore).mask;
    expect(mask).toMatchObject({
      sourceId: 'gal-1',
      width: 320,
      height: 240,
      coverage: 0.25,
      origin: 'restored'
    });
    expect(toast).toHaveBeenCalledWith(get(t).messages.editMaskRestored('25.0'));

    const [, init] = fetchRaw.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.body as FormData).get('gallery_image_id')).toBe('gal-1');
  });

  it('falls back to measureMaskBlob when X-Mask-Coverage is omitted, and returns missing if decode fails', async () => {
    editSourceStore.addFiles([new File([new Uint8Array([9, 8, 7])], 'up.png', { type: 'image/png' })], () => {});
    const uploadId = primaryEditSourceId(get(editSourceStore));
    editSourceStore.updateUploadSize(uploadId, 64, 64);

    const fetchRaw = vi.fn(async () => new Response(pngBlob(64, 64), { status: 200 }));
    const readPngSize = vi.fn(() => ({ width: 64, height: 64 }));
    const measureMaskBlob = vi.fn(async () => ({ width: 64, height: 64, coverage: 0.5 }));
    const toast = vi.fn();

    const session = createEditSession({
      fetchRaw,
      loadMaskHelpers: async (): Promise<MaskImageHelpers> => ({ readPngSize, measureMaskBlob }),
      toast
    });

    expect(await session.restoreMaskFromJob(makeJob())).toBe('restored');
    expect(readPngSize).not.toHaveBeenCalled();
    expect(measureMaskBlob).toHaveBeenCalledTimes(1);
    expect(get(editSourceStore).mask?.coverage).toBe(0.5);

    const [, init] = fetchRaw.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.body as FormData).get('source_sha256')).toMatch(/^[0-9a-f]{64}$/);

    measureMaskBlob.mockRejectedValueOnce(new Error('bad png'));
    expect(await session.restoreMaskFromJob(makeJob())).toBe('missing');
  });

  it('returns mismatch when restored mask dimensions differ from known primary dimensions', async () => {
    editSourceStore.setGallerySource('gal-1', 'Gal 1', 'blob:gal-1', 'gal-1', undefined, 800, 600);
    const toast = vi.fn();
    const session = createEditSession({
      fetchRaw: async () =>
        new Response(pngBlob(640, 480), {
          status: 200,
          headers: { 'X-Mask-Coverage': '0.3' }
        }),
      loadMaskHelpers: async (): Promise<MaskImageHelpers> => ({
        readPngSize: () => ({ width: 640, height: 480 }),
        measureMaskBlob: vi.fn()
      }),
      toast
    });

    expect(await session.restoreMaskFromJob(makeJob())).toBe('mismatch');
    expect(get(editSourceStore).mask).toBeNull();
    expect(toast).not.toHaveBeenCalled();
  });

  it('returns mismatch when the primary source changes while awaiting restore', async () => {
    editSourceStore.setGallerySource('gal-1', 'Gal 1', 'blob:gal-1', 'gal-1', undefined, 320, 240);
    let releaseFetch!: (response: Response) => void;
    const fetchPromise = new Promise<Response>((resolve) => {
      releaseFetch = resolve;
    });
    const toast = vi.fn();
    const session = createEditSession({
      fetchRaw: () => fetchPromise,
      loadMaskHelpers: async (): Promise<MaskImageHelpers> => ({
        readPngSize: () => ({ width: 320, height: 240 }),
        measureMaskBlob: vi.fn()
      }),
      toast
    });

    const pending = session.restoreMaskFromJob(makeJob());
    // Swap primary source mid-flight.
    editSourceStore.setGallerySource('gal-2', 'Gal 2', 'blob:gal-2', 'gal-2', undefined, 320, 240);
    releaseFetch(
      new Response(pngBlob(320, 240), {
        status: 200,
        headers: { 'X-Mask-Coverage': '0.4' }
      })
    );

    expect(await pending).toBe('mismatch');
    expect(get(editSourceStore).mask).toBeNull();
    expect(toast).not.toHaveBeenCalled();
  });

  it('maps HTTP 409 to mismatch and 404 or bare reference primaries to missing', async () => {
    // Bare reference primary cannot be restored
    editSourceStore.addGalleryReferences([
      {
        id: 'ref-1',
        imageId: 'img-1',
        label: 'Ref 1',
        previewUrl: 'blob:ref-1',
        previewLabel: 'Ref 1',
        width: 100,
        height: 100
      }
    ]);
    const fetchRaw = vi.fn();
    const session = createEditSession({
      fetchRaw,
      loadMaskHelpers: async (): Promise<MaskImageHelpers> => ({
        readPngSize: () => null,
        measureMaskBlob: vi.fn()
      })
    });
    expect(await session.restoreMaskFromJob(makeJob())).toBe('missing');
    expect(fetchRaw).not.toHaveBeenCalled();

    // 409 -> mismatch
    editSourceStore.setGallerySource('gal-1', 'Gal 1', 'blob:gal-1', 'gal-1');
    fetchRaw.mockResolvedValueOnce(new Response('conflict', { status: 409 }));
    expect(await session.restoreMaskFromJob(makeJob())).toBe('mismatch');

    // 404 -> missing
    fetchRaw.mockResolvedValueOnce(new Response('not found', { status: 404 }));
    expect(await session.restoreMaskFromJob(makeJob())).toBe('missing');
  });
});

describe('editSession mask and markup apply flows (C3-S5)', () => {
  it('applyEditMask confirms before replacing markup and preserves both on decline', async () => {
    editSourceStore.addFiles([new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })], () => {});
    const primaryId = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMarkup({
      sourceId: primaryId,
      blob: new Blob([new Uint8Array([1])], { type: 'image/png' }),
      width: 16,
      height: 16,
      previewUrl: 'blob:existing-markup',
      origin: 'painted'
    });

    const confirm = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const toast = vi.fn();
    const notices: EditSourceDiscardNotice[] = [];
    const off = editSourceDiscards.onDiscard((n) => notices.push(n));

    try {
      const session = createEditSession({ confirm, toast, revokeObjectURL: revokeSpy });

      // Declining leaves existing markup intact and revokes the candidate mask URL
      const declinedMask = makeMask(primaryId, 'blob:declined-mask');
      expect(await session.applyEditMask(declinedMask)).toBe(false);
      expect(confirm).toHaveBeenCalledWith(
        expect.objectContaining({
          title: get(t).markupEditor.replaceMarkupTitle,
          message: get(t).markupEditor.replaceMarkupMessage
        })
      );
      expect(revokeSpy).toHaveBeenCalledWith('blob:declined-mask');
      expect(get(editSourceStore).markup?.previewUrl).toBe('blob:existing-markup');
      expect(get(editSourceStore).mask).toBeNull();
      expect(toast).not.toHaveBeenCalled();

      // Confirming installs mask and preserves the setMask silent-markup-clear asymmetry
      const acceptedMask = makeMask(primaryId, 'blob:accepted-mask');
      expect(await session.applyEditMask(acceptedMask)).toBe(true);
      expect(get(editSourceStore).mask?.previewUrl).toBe('blob:accepted-mask');
      expect(get(editSourceStore).markup).toBeNull();
      expect(notices).toEqual([]);
      expect(toast).toHaveBeenCalledWith(get(t).messages.editMaskApplied('42.0'));
    } finally {
      off();
    }
  });

  it('applyEditMask refuses and revokes previewUrl if the primary source changed while confirming', async () => {
    editSourceStore.addFiles([new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })], () => {});
    const primaryId = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMarkup({
      sourceId: primaryId,
      blob: new Blob([new Uint8Array([1])], { type: 'image/png' }),
      width: 16,
      height: 16,
      previewUrl: 'blob:existing-markup',
      origin: 'painted'
    });

    let resolveConfirm!: (value: boolean) => void;
    const session = createEditSession({
      confirm: () =>
        new Promise<boolean>((resolve) => {
          resolveConfirm = resolve;
        }),
      revokeObjectURL: revokeSpy
    });

    const pending = session.applyEditMask(makeMask(primaryId, 'blob:stale-mask'));
    editSourceStore.setGallerySource('gal-new', 'New', 'blob:new', 'new');
    resolveConfirm(true);

    expect(await pending).toBe(false);
    expect(revokeSpy).toHaveBeenCalledWith('blob:stale-mask');
    expect(get(editSourceStore).mask).toBeNull();
  });

  it('applyPaintedMarkup validates, confirms mask replacement, and installs atomically', async () => {
    editSourceStore.addFiles([new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })], () => {});
    const primaryId = primaryEditSourceId(get(editSourceStore));
    editSourceStore.setMask(makeMask(primaryId, 'blob:active-mask'));

    const payload: MarkupApplyPayload = {
      sessionId: 1,
      sourceId: primaryId,
      blob: pngBlob(16, 16),
      width: 16,
      height: 16,
      origin: 'painted',
      editorState: { width: 16, height: 16, base: null, commands: [] }
    };

    const validateMarkup = vi.fn(async () => {});
    const confirm = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const toast = vi.fn();
    const notices: EditSourceDiscardNotice[] = [];
    const off = editSourceDiscards.onDiscard((n) => notices.push(n));

    try {
      const session = createEditSession({ confirm, toast, validateMarkup });

      // Declining mask replacement leaves mask untouched and shows no toast
      expect(
        await session.applyPaintedMarkup(payload, {
          expectedMarkupRef: null,
          isEditorCurrent: () => true
        })
      ).toBe(false);
      expect(get(editSourceStore).mask?.previewUrl).toBe('blob:active-mask');
      expect(get(editSourceStore).markup).toBeNull();
      expect(toast).not.toHaveBeenCalled();

      // Confirming installs markup, replaces mask (emitting discard notice), and toasts applied
      expect(
        await session.applyPaintedMarkup(payload, {
          expectedMarkupRef: null,
          isEditorCurrent: () => true
        })
      ).toBe(true);
      expect(get(editSourceStore).mask).toBeNull();
      expect(get(editSourceStore).markup).toMatchObject({
        sourceId: primaryId,
        origin: 'painted'
      });
      expect(notices).toEqual([{ seq: 1, kind: 'mask', reason: 'replaced' }]);
      expect(toast).toHaveBeenCalledWith(get(t).markupEditor.applied);
    } finally {
      off();
    }
  });

  it('applyPaintedMarkup aborts cleanly when validation fails or the session becomes stale mid-flight', async () => {
    editSourceStore.addFiles([new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })], () => {});
    const primaryId = primaryEditSourceId(get(editSourceStore));
    const payload: MarkupApplyPayload = {
      sessionId: 1,
      sourceId: primaryId,
      blob: pngBlob(16, 16),
      width: 16,
      height: 16,
      origin: 'painted',
      editorState: { width: 16, height: 16, base: null, commands: [] }
    };

    const toast = vi.fn();
    let editorCurrent = true;
    const session = createEditSession({
      toast,
      validateMarkup: async () => {
        editorCurrent = false;
      }
    });

    expect(
      await session.applyPaintedMarkup(payload, {
        expectedMarkupRef: null,
        isEditorCurrent: () => editorCurrent
      })
    ).toBe(false);
    expect(toast).toHaveBeenCalledWith(get(t).markupEditor.errorSourceChanged, 'error');
    expect(get(editSourceStore).markup).toBeNull();

    const failingSession = createEditSession({
      toast,
      validateMarkup: async () => {
        throw new MarkupRejectedError('opaque');
      }
    });
    toast.mockClear();
    expect(
      await failingSession.applyPaintedMarkup(payload, {
        expectedMarkupRef: null,
        isEditorCurrent: () => true
      })
    ).toBe(false);
    expect(toast).toHaveBeenCalledWith(get(t).markupEditor.errorOpaque, 'error');
  });
});
