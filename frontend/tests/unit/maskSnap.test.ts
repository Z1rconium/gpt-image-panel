import { describe, expect, it } from 'vitest';
import {
  SNAP_MAX_EDGE,
  computeEdgeMap,
  edgeMapSize,
  snapToEdge,
  type EdgeMap
} from '$lib/features/mask/maskSnap';

/** Build an RGBA buffer from a grayscale shade function. */
function rgbaBuffer(
  width: number,
  height: number,
  shade: (x: number, y: number) => number
): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const value = shade(x, y);
      const pixel = (y * width + x) * 4;
      data[pixel] = value;
      data[pixel + 1] = value;
      data[pixel + 2] = value;
      data[pixel + 3] = 255;
    }
  }
  return data;
}

function edgeMapFrom(width: number, height: number, magnitude: Uint8Array): EdgeMap {
  return { width, height, scale: 1, sourceWidth: width, sourceHeight: height, magnitude };
}

describe('edgeMapSize', () => {
  it('caps the long edge without upscaling', () => {
    const capped = edgeMapSize(4096, 2048, 1600);
    expect(capped.width).toBe(1600);
    expect(capped.height).toBe(800);
    expect(capped.scale).toBeCloseTo(1600 / 4096, 10);

    expect(edgeMapSize(800, 600)).toEqual({ width: 800, height: 600, scale: 1 });
  });

  it('falls back to 1x1 for a degenerate source', () => {
    expect(edgeMapSize(0, 0)).toEqual({ width: 1, height: 1, scale: 1 });
    expect(edgeMapSize(Number.NaN, 10)).toEqual({ width: 1, height: 1, scale: 1 });
  });

  it('uses the default cap when none is given', () => {
    expect(edgeMapSize(3200, 3200).width).toBe(SNAP_MAX_EDGE);
  });
});

describe('computeEdgeMap', () => {
  it('marks a hard vertical step as a full-strength edge and leaves flat areas empty', () => {
    const data = rgbaBuffer(16, 16, (x) => (x < 8 ? 0 : 255));
    const map = computeEdgeMap(data, 16, 16);

    expect(map.magnitude[8 * 16 + 7]).toBe(255);
    expect(map.magnitude[8 * 16 + 8]).toBe(255);
    expect(map.magnitude[8 * 16 + 3]).toBe(0);
    expect(map.magnitude[8 * 16 + 13]).toBe(0);
  });

  it('forces the outermost ring to the maximum so the picture frame snaps', () => {
    const map = computeEdgeMap(rgbaBuffer(8, 8, () => 128), 8, 8);

    expect(map.magnitude[0]).toBe(255);
    expect(map.magnitude[7]).toBe(255);
    expect(map.magnitude[7 * 8]).toBe(255);
    expect(map.magnitude[7 * 8 + 7]).toBe(255);
  });

  it('records the scale and source size for coordinate mapping', () => {
    const map = computeEdgeMap(new Uint8ClampedArray(4 * 4 * 4), 4, 4, {
      scale: 0.25,
      sourceWidth: 4000,
      sourceHeight: 3000
    });

    expect(map.scale).toBe(0.25);
    expect(map.sourceWidth).toBe(4000);
    expect(map.sourceHeight).toBe(3000);
  });

  it('returns an empty map when the buffer does not match the dimensions', () => {
    const map = computeEdgeMap(new Uint8ClampedArray(4), 8, 8);

    expect(map.magnitude).toHaveLength(64);
    expect(Array.from(map.magnitude).every((value) => value === 0)).toBe(true);
  });
});

describe('snapToEdge', () => {
  const stepMap = computeEdgeMap(rgbaBuffer(16, 16, (x) => (x < 8 ? 0 : 255)), 16, 16);

  it('pulls a nearby point onto the strongest edge', () => {
    const result = snapToEdge(stepMap, 6, 8, 4);

    expect(result.snapped).toBe(true);
    expect(result.strength).toBe(255);
    expect(result.x).toBeCloseTo(7.5, 6);
    // The edge column is strong on every row, so the snapped row is whichever
    // neighbour is equally close to the cursor.
    expect(Math.abs(result.y - 8)).toBeLessThanOrEqual(0.51);
  });

  it('leaves a point alone when the radius reaches no edge', () => {
    const result = snapToEdge(stepMap, 2, 8, 1);

    expect(result.snapped).toBe(false);
    expect(result.x).toBe(2);
    expect(result.y).toBe(8);
  });

  it('leaves flat area alone but still snaps to the picture frame', () => {
    const flat = computeEdgeMap(rgbaBuffer(16, 16, () => 128), 16, 16);

    const middle = snapToEdge(flat, 8, 8, 2);
    expect(middle.snapped).toBe(false);
    expect(middle.x).toBe(8);

    const nearFrame = snapToEdge(flat, 1, 8, 4);
    expect(nearFrame.snapped).toBe(true);
    expect(nearFrame.x).toBeCloseTo(0.5, 6);
  });

  it('honours the strength threshold', () => {
    const soft = computeEdgeMap(rgbaBuffer(16, 16, (x) => (x < 8 ? 0 : 40)), 16, 16);
    expect(soft.magnitude[8 * 16 + 8]).toBe(40);

    expect(snapToEdge(soft, 6, 8, 4).snapped).toBe(false);
    expect(snapToEdge(soft, 6, 8, 4, 10).snapped).toBe(true);
  });

  it('maps source coordinates through a downscaled map', () => {
    const magnitude = new Uint8Array(1000 * 1000);
    for (let y = 0; y < 1000; y += 1) magnitude[y * 1000 + 500] = 255;
    const map: EdgeMap = {
      width: 1000,
      height: 1000,
      scale: 0.25,
      sourceWidth: 4000,
      sourceHeight: 4000,
      magnitude
    };

    // radius 40 source px -> 10 map px, wide enough to reach map column 500.
    const wide = snapToEdge(map, 1990, 1990, 40);
    expect(wide.snapped).toBe(true);
    expect(wide.x).toBeCloseTo((500 + 0.5) * 4, 6);

    // radius 4 source px -> 1 map px, too narrow to reach the line.
    expect(snapToEdge(map, 1990, 1990, 4).snapped).toBe(false);
  });

  it('stays on the edge the previous point chose when a parallel one is equally strong', () => {
    // Two rows of identical strength, one 2px above the cursor and one 2px
    // below: without the continuity anchor the cursor's own row wins on the
    // distance penalty, and the selection combs between the two.
    const width = 21;
    const map = edgeMapFrom(width, 11, new Uint8Array(width * 11));
    for (let x = 0; x < width; x += 1) {
      map.magnitude[4 * width + x] = 200;
      map.magnitude[8 * width + x] = 200;
    }
    const cursor = { x: 10, y: 6 };

    const cold = snapToEdge(map, cursor.x, cursor.y, 4);
    expect(cold.snapped).toBe(true);
    expect(cold.y).toBeCloseTo(4.5, 6);

    const anchored = snapToEdge(map, cursor.x, cursor.y, 4, undefined, {
      x: 10.5,
      y: 8.5,
      stepImagePx: 1
    });
    expect(anchored.snapped).toBe(true);
    expect(anchored.y).toBeCloseTo(8.5, 6);
  });

  it('prefers a nearer edge of comparable strength over a distant one', () => {
    // The distant edge is stronger (210 vs 200); without the distance penalty
    // it would win. Both are inside the radius.
    const map = edgeMapFrom(21, 1, new Uint8Array(21));
    map.magnitude[4] = 200;
    map.magnitude[10] = 210;

    const result = snapToEdge(map, 4, 0, 8);
    expect(result.snapped).toBe(true);
    expect(result.x).toBeCloseTo(4.5, 6);
  });
});
