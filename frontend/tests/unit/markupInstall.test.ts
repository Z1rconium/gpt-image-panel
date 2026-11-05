import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { editSourceStore, initialEditSourceState, primaryEditSourceId } from '$lib/stores/editSource';
import { installMarkup, MarkupRejectedError, markupRejectionMessage, validatePaintedMarkup } from '$lib/features/workspace/markup';

function pngHeader(width: number, height: number, signature = [137, 80, 78, 71, 13, 10, 26, 10]) {
  const bytes = new Uint8Array(33);
  bytes.set(signature, 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([73, 72, 68, 82], 12);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return new Blob([bytes], { type: 'image/png' });
}

let revokeSpy = vi.fn<(url: string) => void>();

beforeEach(() => {
  revokeSpy = vi.fn<(url: string) => void>();
  let counter = 0;
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => `blob:test-${(counter += 1)}`;
  (URL as unknown as { revokeObjectURL: (url: string) => void }).revokeObjectURL = revokeSpy;
  editSourceStore.set({ ...initialEditSourceState });
  editSourceStore.addFiles([new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })], () => {});
});

describe('painted markup validation', () => {
  it('accepts a PNG whose header matches the exported size', async () => {
    await expect(validatePaintedMarkup(pngHeader(640, 480), 640, 480)).resolves.toBeUndefined();
  });

  it('names the reason when the bytes are not the PNG the editor exported', async () => {
    await expect(validatePaintedMarkup(pngHeader(10, 10, [1, 2, 3, 4, 5, 6, 7, 8]), 10, 10)).rejects.toMatchObject({ reason: 'not_png' });
    await expect(validatePaintedMarkup(pngHeader(640, 479), 640, 480)).rejects.toMatchObject({ reason: 'size_mismatch' });
    await expect(validatePaintedMarkup(new Blob([new Uint8Array([1, 2])]), 1, 1)).rejects.toMatchObject({ reason: 'not_png' });
  });

  it('enforces the 16M-pixel and 50 MiB limits with their own reasons', async () => {
    await expect(validatePaintedMarkup(pngHeader(4097, 4096), 4097, 4096)).rejects.toMatchObject({ reason: 'pixels' });
    const huge = pngHeader(8, 8);
    Object.defineProperty(huge, 'size', { value: 50 * 1024 * 1024 });
    await expect(validatePaintedMarkup(huge, 8, 8)).rejects.toMatchObject({ reason: 'file_size' });
    await expect(validatePaintedMarkup(pngHeader(4096, 4096), 4096, 4096)).resolves.toBeUndefined();
  });

  it('turns rejections into specific localised messages', () => {
    const messages = new Set(
      (['file_size', 'pixels', 'source', 'opaque', 'not_png'] as const).map((reason) => markupRejectionMessage(new MarkupRejectedError(reason)))
    );
    // not_png falls back to the generic invalid message; the others are all distinct.
    expect(messages.size).toBe(5);
    expect(markupRejectionMessage(new Error('boom'))).not.toBe('');
  });
});

describe('installMarkup (atomic swap)', () => {
  const blob = () => new Blob([new Uint8Array([1])], { type: 'image/png' });

  it('installs for the current primary and replaces the previous markup once', () => {
    const id = primaryEditSourceId(get(editSourceStore));
    installMarkup({ sourceId: id, blob: blob(), width: 8, height: 8, origin: 'painted' });
    const first = get(editSourceStore).markup?.previewUrl;
    installMarkup({ sourceId: id, blob: blob(), width: 8, height: 8, origin: 'painted' });
    expect(get(editSourceStore).markup?.previewUrl).not.toBe(first);
    expect(revokeSpy.mock.calls.filter(([url]) => url === first)).toHaveLength(1);
  });

  it('refuses a stale source and leaves the existing markup untouched', () => {
    const id = primaryEditSourceId(get(editSourceStore));
    installMarkup({ sourceId: id, blob: blob(), width: 8, height: 8, origin: 'uploaded' });
    const before = get(editSourceStore).markup;
    expect(() => installMarkup({ sourceId: 'another-image', blob: blob(), width: 8, height: 8, origin: 'painted' })).toThrow(MarkupRejectedError);
    expect(get(editSourceStore).markup).toBe(before);
    expect(revokeSpy).not.toHaveBeenCalled();
  });

  it('refuses when the caller session is no longer current, without creating an object URL', () => {
    const id = primaryEditSourceId(get(editSourceStore));
    const create = vi.spyOn(URL as unknown as { createObjectURL: () => string }, 'createObjectURL');
    expect(() => installMarkup({ sourceId: id, blob: blob(), width: 8, height: 8, origin: 'painted' }, () => false)).toThrowError(
      expect.objectContaining({ reason: 'source' })
    );
    expect(create).not.toHaveBeenCalled();
    expect(get(editSourceStore).markup).toBeNull();
  });

  it('keeps legacy callers working: origin defaults are optional on the stored shape', () => {
    const id = primaryEditSourceId(get(editSourceStore));
    installMarkup({ sourceId: id, blob: blob(), width: 8, height: 8, origin: 'restored' });
    expect(get(editSourceStore).markup?.origin).toBe('restored');
    expect(get(editSourceStore).markup?.editorState).toBeUndefined();
  });
});
