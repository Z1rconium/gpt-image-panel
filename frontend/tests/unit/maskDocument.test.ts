import { describe, expect, it } from 'vitest';
import {
  MaskImportError,
  binarizeMaskAlphaData,
  countMarkedPixels,
  normalizeFeatherPx,
  pngHasAlphaChannel
} from '$lib/features/mask/maskDocument';

function pngHeader(colorType: number, extra: number[] = []): Uint8Array {
  const bytes = new Uint8Array(33 + extra.length);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  bytes[25] = colorType;
  if (extra.length) bytes.set(extra, 33);
  return bytes;
}

describe('binarizeMaskAlphaData', () => {
  it('turns alpha below 128 into 0 (editable) and everything else into 255', () => {
    const data = new Uint8ClampedArray([
      10, 20, 30, 0, 10, 20, 30, 127, 10, 20, 30, 128, 10, 20, 30, 255
    ]);

    const coverage = binarizeMaskAlphaData(data);

    expect(Array.from(data.filter((_, index) => index % 4 === 3))).toEqual([0, 0, 255, 255]);
    expect(coverage).toBe(0.5);
  });

  it('returns zero for empty data', () => {
    expect(binarizeMaskAlphaData(new Uint8ClampedArray(0))).toBe(0);
  });
});

describe('countMarkedPixels', () => {
  it('counts fully marked pixels at or above the 128 threshold', () => {
    const data = new Uint8ClampedArray([0, 0, 0, 0, 0, 0, 0, 127, 0, 0, 0, 128, 0, 0, 0, 255]);
    expect(countMarkedPixels(data)).toBe(0.5);
  });
});

describe('pngHasAlphaChannel', () => {
  it('accepts truecolor and grayscale images with an alpha channel', () => {
    expect(pngHasAlphaChannel(pngHeader(6))).toBe(true);
    expect(pngHasAlphaChannel(pngHeader(4))).toBe(true);
  });

  it('rejects truecolor and grayscale images without alpha', () => {
    expect(pngHasAlphaChannel(pngHeader(2))).toBe(false);
    expect(pngHasAlphaChannel(pngHeader(0))).toBe(false);
  });

  it('accepts palette images only when a tRNS chunk is present', () => {
    expect(pngHasAlphaChannel(pngHeader(3))).toBe(false);
    expect(pngHasAlphaChannel(pngHeader(3, [0x74, 0x52, 0x4e, 0x53]))).toBe(true);
  });

  it('rejects data that is not a PNG', () => {
    expect(pngHasAlphaChannel(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26]))).toBe(false);
    expect(pngHasAlphaChannel(new Uint8Array([0x89, 0x50]))).toBe(false);
  });
});

describe('normalizeFeatherPx', () => {
  it('rounds valid radii and rejects non-positive or non-finite input', () => {
    expect(normalizeFeatherPx(9.4, 1024)).toBe(9);
    expect(normalizeFeatherPx(0, 1024)).toBe(0);
    expect(normalizeFeatherPx(-8, 1024)).toBe(0);
    expect(normalizeFeatherPx(Number.NaN, 1024)).toBe(0);
  });

  it('caps the radius at the maximum and at an eighth of the image width', () => {
    expect(normalizeFeatherPx(500, 1024)).toBe(64);
    expect(normalizeFeatherPx(64, 64)).toBe(8);
    expect(normalizeFeatherPx(64, 16)).toBe(2);
  });
});

describe('MaskImportError', () => {
  it('carries the code and decoded size', () => {
    const error = new MaskImportError('size', { width: 32, height: 32 });
    expect(error.code).toBe('size');
    expect(error.width).toBe(32);
    expect(error.height).toBe(32);
    expect(error).toBeInstanceOf(Error);
  });

  it('defaults the size to zero', () => {
    const error = new MaskImportError('not-png');
    expect(error.width).toBe(0);
    expect(error.height).toBe(0);
  });
});
