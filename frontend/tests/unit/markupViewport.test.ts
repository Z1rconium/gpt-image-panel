import { describe, expect, it } from 'vitest';
import {
  MAX_VIEW_SCALE,
  clampToImage,
  clampView,
  fitScale,
  fitView,
  imageToStage,
  isInsideImage,
  minViewScale,
  panView,
  pinchView,
  stageToImage,
  viewportBackingSize,
  zoomViewAt,
  zoomViewByFactor
} from '$lib/features/markup/markupViewport';

const stage = { width: 800, height: 600 };

describe('markup viewport fit', () => {
  it('fits an image into the stage with padding', () => {
    const scale = fitScale(stage, { width: 2000, height: 1000 });
    expect(scale).toBeCloseTo((800 - 24) / 2000, 6);
    const view = fitView(stage, { width: 2000, height: 1000 });
    expect(view.scale).toBeCloseTo(scale, 6);
    // Centered on both axes.
    expect(view.x).toBeCloseTo((800 - 2000 * scale) / 2, 6);
    expect(view.y).toBeCloseTo((600 - 1000 * scale) / 2, 6);
  });

  it('can show an extremely wide image in full instead of clamping to a fixed minimum zoom', () => {
    const image = { width: 30000, height: 400 };
    const view = fitView(stage, image);
    expect(view.scale).toBeLessThan(0.05);
    expect(view.scale * image.width).toBeLessThanOrEqual(stage.width);
    // The minimum allowed zoom must not exceed the fit zoom.
    expect(minViewScale(view.scale)).toBeLessThanOrEqual(view.scale);
    const zoomedOut = zoomViewByFactor(view, 0.01, { x: 400, y: 300 }, stage, image);
    expect(zoomedOut.scale).toBeGreaterThan(0);
    expect(zoomedOut.scale).toBeGreaterThanOrEqual(minViewScale(view.scale));
  });

  it('enlarges small images to fit and still caps zooming in', () => {
    const view = fitView(stage, { width: 100, height: 100 });
    expect(view.scale).toBeGreaterThan(1);
    const zoomed = zoomViewAt(view, 1000, { x: 400, y: 300 }, stage, { width: 100, height: 100 });
    expect(zoomed.scale).toBe(MAX_VIEW_SCALE);
  });

  it('returns a harmless scale for degenerate sizes', () => {
    expect(fitScale({ width: 0, height: 0 }, { width: 10, height: 10 })).toBe(1);
    expect(fitScale(stage, { width: 0, height: 10 })).toBe(1);
  });
});

describe('markup viewport coordinates', () => {
  it('round-trips between stage and image coordinates', () => {
    const view = { scale: 0.37, x: 41, y: -12 };
    const point = { x: 1234.5, y: 678.25 };
    const back = stageToImage(view, imageToStage(view, point));
    expect(back.x).toBeCloseTo(point.x, 9);
    expect(back.y).toBeCloseTo(point.y, 9);
  });

  it('keeps the point under the cursor fixed while zooming', () => {
    const image = { width: 4000, height: 3000 };
    const view = fitView(stage, image);
    const anchor = { x: 250, y: 310 };
    const before = stageToImage(view, anchor);
    const zoomed = zoomViewAt(view, view.scale * 3, anchor, stage, image);
    const after = stageToImage(zoomed, anchor);
    expect(after.x).toBeCloseTo(before.x, 4);
    expect(after.y).toBeCloseTo(before.y, 4);
  });

  it('is independent of the device pixel ratio', () => {
    const image = { width: 4096, height: 4096 };
    const view = fitView(stage, image);
    const at1 = viewportBackingSize(stage, 1);
    const at2 = viewportBackingSize(stage, 2);
    expect(at2.width).toBe(at1.width * 2);
    // Coordinates are CSS pixels only; the backing store size is separate and bounded by the stage.
    expect(viewportBackingSize(stage, 2).width).toBeLessThan(image.width);
    expect(stageToImage(view, imageToStage(view, { x: 10, y: 20 }))).toEqual({ x: 10, y: 20 });
  });

  it('caps the backing store regardless of stage size', () => {
    const size = viewportBackingSize({ width: 6000, height: 3000 }, 3, 4096);
    expect(Math.max(size.width, size.height)).toBeLessThanOrEqual(4096);
  });

  it('clips points to the image', () => {
    const image = { width: 100, height: 50 };
    expect(clampToImage({ x: -5, y: 70 }, image)).toEqual({ x: 0, y: 50 });
    expect(isInsideImage({ x: 100, y: 50 }, image)).toBe(true);
    expect(isInsideImage({ x: 100.1, y: 10 }, image)).toBe(false);
  });
});

describe('markup viewport panning and gestures', () => {
  const image = { width: 4000, height: 3000 };

  it('limits panning near the image edges', () => {
    const view = { scale: 1, x: 0, y: 0 };
    const far = panView(view, 10_000, 10_000, stage, image);
    expect(far.x).toBeLessThanOrEqual(64);
    expect(far.y).toBeLessThanOrEqual(64);
    const other = panView(view, -100_000, -100_000, stage, image);
    expect(other.x).toBeGreaterThanOrEqual(stage.width - image.width - 64);
    expect(other.y).toBeGreaterThanOrEqual(stage.height - image.height - 64);
  });

  it('centers an image smaller than the stage on each axis', () => {
    const clamped = clampView({ scale: 1, x: 500, y: -500 }, stage, { width: 200, height: 100 });
    expect(clamped.x).toBe((800 - 200) / 2);
    expect(clamped.y).toBe((600 - 100) / 2);
  });

  it('pinch zooms around the gesture midpoint and follows its movement', () => {
    const start = { scale: 0.5, x: 20, y: 40 };
    const startCenter = { x: 300, y: 200 };
    const anchorImage = stageToImage(start, startCenter);
    const next = pinchView(start, startCenter, 100, { x: 340, y: 230 }, 200, stage, image);
    expect(next.scale).toBeCloseTo(1, 6);
    const moved = imageToStage(next, anchorImage);
    expect(moved.x).toBeCloseTo(340, 3);
    expect(moved.y).toBeCloseTo(230, 3);
  });

  it('ignores a pinch with a degenerate distance', () => {
    const start = { scale: 0.5, x: 20, y: 40 };
    expect(pinchView(start, { x: 0, y: 0 }, 0, { x: 5, y: 5 }, 10, stage, image)).toBe(start);
  });
});
