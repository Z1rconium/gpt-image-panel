import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { initialEditSourceState, uploadSources, type EditGallerySource, type EditSourceState } from '$lib/stores/editSource';
import {
  decodePrimaryBlob,
  fetchPrimaryImageBlob,
  loadPrimaryImage,
  PrimaryImageError,
  readPrimaryDimensions
} from '$lib/features/workspace/editPrimaryImage';

const upload = (width = 0, height = 0): EditSourceState => ({
  ...initialEditSourceState,
  sources: [
    {
      kind: 'upload',
      id: 'upload-1',
      file: new File([new Uint8Array([1, 2, 3])], 'a.png', { type: 'image/png' }),
      label: 'a.png',
      previewUrl: 'blob:a',
      previewLabel: 'a.png',
      width,
      height
    }
  ]
});

const gallery = (overrides: Partial<EditGallerySource> = {}): EditSourceState => ({
  ...initialEditSourceState,
  sources: [
    {
      kind: 'gallery_primary',
      id: 'gallery-1',
      imageId: 'gallery-1',
      filename: 'original.jpg',
      label: 'Gallery',
      previewUrl: 'https://cdn.invalid/thumb?sig=secret',
      previewLabel: 'Gallery',
      width: 64,
      height: 48,
      ...overrides
    }
  ]
});

let fetchMock: ReturnType<typeof vi.fn>;
let bitmapSize = { width: 300, height: 400 };
let closed = 0;

beforeEach(() => {
  closed = 0;
  bitmapSize = { width: 300, height: 400 };
  fetchMock = vi.fn(async () => new Response(new Uint8Array([9, 9, 9]), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal(
    'createImageBitmap',
    vi.fn(async () => ({ ...bitmapSize, close: () => (closed += 1) }))
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('primary image source', () => {
  it('uses the picked upload without any network request', async () => {
    const state = upload();
    const result = await fetchPrimaryImageBlob(state);
    expect(result.sourceId).toBe('upload-1');
    expect(result.blob).toBe(uploadSources(state)[0].file);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reads a gallery source from the same-origin original, never from the CDN preview', async () => {
    await fetchPrimaryImageBlob(gallery());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/image/original.jpg');
    expect(String(url)).not.toContain('cdn.invalid');
    expect((init as RequestInit).credentials).toBe('same-origin');
  });

  it('looks the filename up when the gallery entry does not carry one', async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ filename: 'looked up.png' }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(new Uint8Array([1]), { status: 200 }));
    await fetchPrimaryImageBlob(gallery({ filename: '' }));
    expect(fetchMock.mock.calls[0][0]).toBe('/api/gallery/gallery-1');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/image/looked%20up.png');
  });

  it('fails when there is no primary image or the original cannot be fetched', async () => {
    await expect(fetchPrimaryImageBlob(initialEditSourceState)).rejects.toBeInstanceOf(PrimaryImageError);
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 404 }));
    await expect(fetchPrimaryImageBlob(gallery())).rejects.toBeInstanceOf(PrimaryImageError);
  });
});

describe('primary image decoding', () => {
  it('reports the decoded (upright) size, not the stored metadata, and releases the bitmap', async () => {
    const size = await readPrimaryDimensions(gallery({ width: 400, height: 300 }));
    expect(size).toEqual({ sourceId: 'gallery-1', width: 300, height: 400 });
    expect(closed).toBe(1);
  });

  it('rejects an undecodable or empty image', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => Promise.reject(new Error('bad'))));
    await expect(decodePrimaryBlob(new Blob([new Uint8Array([1])]))).rejects.toBeInstanceOf(PrimaryImageError);
    bitmapSize = { width: 0, height: 0 };
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ ...bitmapSize, close: () => (closed += 1) })));
    await expect(decodePrimaryBlob(new Blob([new Uint8Array([1])]))).rejects.toBeInstanceOf(PrimaryImageError);
    expect(closed).toBe(1);
  });

  it('closes a late bitmap and rejects when the caller aborted mid-flight', async () => {
    const controller = new AbortController();
    let release: () => void = () => {};
    vi.stubGlobal(
      'createImageBitmap',
      vi.fn(
        () =>
          new Promise((resolve) => {
            release = () => resolve({ ...bitmapSize, close: () => (closed += 1) });
          })
      )
    );
    const pending = loadPrimaryImage(upload(), controller.signal);
    await Promise.resolve();
    await Promise.resolve();
    controller.abort();
    release();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(closed).toBe(1);
  });

  it('returns the bitmap and size for the live session', async () => {
    const image = await loadPrimaryImage(upload());
    expect(image).toMatchObject({ sourceId: 'upload-1', width: 300, height: 400 });
    expect(closed).toBe(0);
    image.bitmap.close();
  });
});
