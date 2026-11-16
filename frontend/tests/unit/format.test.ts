import { describe, expect, it } from 'vitest';
import { isSvgMedia, originalUrlForThumbnail, revertThumbnailToOriginal } from '$lib/utils/format';

describe('isSvgMedia', () => {
  it('detects SVG by output format or filename', () => {
    expect(isSvgMedia({ filename: 'a.SVG' })).toBe(true);
    expect(isSvgMedia({ filename: 'a.png', output_format: 'svg' })).toBe(true);
    expect(isSvgMedia({ filename: 'a.png', output_format: 'png' })).toBe(false);
    expect(isSvgMedia(null)).toBe(false);
  });
});

describe('thumbnail fallback', () => {
  it('maps a thumbnail URL to the original image URL only', () => {
    expect(originalUrlForThumbnail('/api/thumb/a%20b.png')).toBe('/api/image/a%20b.png');
    expect(originalUrlForThumbnail('/api/image/a.png')).toBeNull();
    expect(originalUrlForThumbnail('https://cdn.example.com/a.png')).toBeNull();
    expect(originalUrlForThumbnail(null)).toBeNull();
  });

  it('swaps a failed thumbnail for the original once', () => {
    const image = { dataset: {} as Record<string, string>, src: '', getAttribute: () => '/api/thumb/a.png' };
    revertThumbnailToOriginal(image as unknown as HTMLImageElement);
    expect(image.src).toBe('/api/image/a.png');
    image.src = 'changed';
    revertThumbnailToOriginal(image as unknown as HTMLImageElement);
    expect(image.src).toBe('changed');
  });
});
