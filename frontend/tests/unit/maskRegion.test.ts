import { describe, expect, it } from 'vitest';
import {
  bandWatershed,
  chamfer34,
  closeGaps,
  composeRegion,
  cropRegion,
  fillHoles,
  growWithin,
  regionParamsFor,
  snapToBorder,
  type MaskRegionParams,
  type MaskRegionPreferences
} from '$lib/features/mask/maskRegion';
import type { EdgeMap } from '$lib/features/mask/maskSnap';

const BASE_PREFS: MaskRegionPreferences = {
  enabled: true,
  strength: 'normal',
  edgeSnap: false,
  edgeRange: null,
  grow: 0
};

/** Build a binary mask from a predicate (any truthy value marks the pixel). */
function grid(width: number, height: number, shade: (x: number, y: number) => unknown): Uint8Array {
  const data = new Uint8Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) data[y * width + x] = shade(x, y) ? 1 : 0;
  }
  return data;
}

function markedCount(data: Uint8Array): number {
  let count = 0;
  for (let index = 0; index < data.length; index += 1) count += data[index] ? 1 : 0;
  return count;
}

function contains(outer: Uint8Array, inner: Uint8Array): boolean {
  for (let index = 0; index < outer.length; index += 1) {
    if (inner[index] && !outer[index]) return false;
  }
  return true;
}

function filledRect(data: Uint8Array, width: number, rect: { x: number; y: number; width: number; height: number }) {
  for (let y = rect.y; y < rect.y + rect.height; y += 1) {
    for (let x = rect.x; x < rect.x + rect.width; x += 1) data[y * width + x] = 1;
  }
}

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe('regionParamsFor', () => {
  it('scales the distances with the short side and the strength', () => {
    const normal = regionParamsFor(1024, 1024, BASE_PREFS);
    expect(normal.close).toBe(3);
    expect(normal.borderSnap).toBe(10);
    expect(normal.holeRadius).toBe(15);
    expect(normal.edgeRange).toBe(20);

    const light = regionParamsFor(1024, 1024, { ...BASE_PREFS, strength: 'light' });
    const strong = regionParamsFor(1024, 1024, { ...BASE_PREFS, strength: 'strong' });
    expect(light.close).toBe(2);
    expect(light.holeRadius).toBe(8);
    expect(strong.close).toBe(6);
    expect(strong.holeRadius).toBe(31);
  });

  it('keeps floors on tiny images and clamps the explicit values', () => {
    const tiny = regionParamsFor(64, 64, BASE_PREFS);
    expect(tiny.close).toBe(1);
    expect(tiny.borderSnap).toBe(2);
    expect(tiny.holeRadius).toBe(2);

    const clamped = regionParamsFor(4096, 4096, { ...BASE_PREFS, grow: 999, edgeRange: 9999 });
    expect(clamped.grow).toBe(32);
    expect(clamped.edgeRange).toBe(64);
  });
});

describe('chamfer34', () => {
  it('measures 3 per 4-neighbour step and 4 per diagonal, in thirds of a pixel', () => {
    const data = new Uint8Array(9);
    data[4] = 1; // centre of a 3x3
    const distance = chamfer34(data, 3, 3);

    expect(distance[4]).toBe(0);
    expect(distance[1]).toBe(3);
    expect(distance[3]).toBe(3);
    expect(distance[0]).toBe(4);
  });
});

describe('closeGaps', () => {
  it('joins a seam narrower than twice the radius and leaves a wider one alone', () => {
    const size = 64;
    const bars = (gap: number) =>
      grid(size, size, (x, y) => {
        if (y < 8 || y > 55) return 0;
        return (x >= 12 && x < 17) || (x >= 17 + gap && x < 22 + gap);
      });

    const narrow = closeGaps(bars(3), size, size, 2);
    // The 3px seam between the two bars is bridged at the rows both bars cover.
    expect(narrow[32 * size + 18]).toBe(1);
    expect(narrow[32 * size + 19]).toBe(1);

    const wide = closeGaps(bars(10), size, size, 2);
    expect(wide[32 * size + 20]).toBe(0);
  });

  it('never removes a marked pixel, on random masks', () => {
    const width = 48;
    const height = 40;
    const random = mulberry32(20260923);
    for (let round = 0; round < 6; round += 1) {
      const source = grid(width, height, () => random() < 0.35);
      for (const radius of [1, 2, 5]) {
        expect(contains(closeGaps(source, width, height, radius), source)).toBe(true);
      }
    }
  });

  it('is idempotent', () => {
    const source = grid(48, 40, (x, y) => (x * 7 + y * 13) % 11 < 4);
    const once = closeGaps(source, 48, 40, 3);
    const twice = closeGaps(once, 48, 40, 3);
    expect(Array.from(twice)).toEqual(Array.from(once));
  });
});

describe('snapToBorder', () => {
  it('bridges a strip that stops just short of the frame and ignores a distant one', () => {
    const size = 40;
    // Rows 10..29 hold a bar that starts 3px from the left edge, rows 0..9 a
    // bar that starts 10px in.
    const source = grid(size, size, (x, y) =>
      y >= 10 && y < 30 ? x >= 3 && x < 20 : y < 10 ? x >= 10 && x < 20 : false
    );

    const snapped = snapToBorder(source, size, size, 4);
    expect(snapped[20 * size + 0]).toBe(1);
    expect(snapped[20 * size + 2]).toBe(1);
    // The distant bar is untouched: only columns that really are near the
    // frame are pulled in.
    expect(snapped[4 * size + 0]).toBe(0);
    expect(snapped[4 * size + 9]).toBe(0);
  });

  it('reaches all four edges independently', () => {
    const size = 40;
    const source = grid(size, size, (x, y) => (x >= 18 && x < 20 && y >= 2 && y < 37) || false);
    const snapped = snapToBorder(source, size, size, 5);
    expect(snapped[0 * size + 18]).toBe(1);
    expect(snapped[39 * size + 18]).toBe(1);
  });
});

describe('fillHoles', () => {
  it('fills an enclosed pinhole and leaves one that reaches the frame', () => {
    const size = 40;
    const enclosed = grid(size, size, (x, y) => x >= 6 && x <= 24 && y >= 6 && y <= 24 && !(x >= 14 && x <= 16 && y >= 14 && y <= 16));
    const distance = chamfer34(enclosed, size, size);
    const filled = fillHoles(enclosed, size, size, 3, null, distance);
    expect(filled[15 * size + 15]).toBe(1);

    // The same notch, opened through the bottom wall, is not a hole at all:
    // the flood from the frame already reaches every pixel of it.
    const open = grid(size, size, (x, y) => x >= 6 && x <= 24 && y >= 6 && y <= 24 && !(x >= 14 && x <= 16 && y >= 14));
    const openDistance = chamfer34(open, size, size);
    const untouched = fillHoles(open, size, size, 3, null, openDistance);
    expect(Array.from(untouched)).toEqual(Array.from(open));
  });

  it('keeps holes whose inradius is larger than the radius', () => {
    const size = 60;
    const shell = grid(size, size, (x, y) => y >= 5 && y <= 55 && x >= 5 && x <= 55 && !(x >= 25 && x <= 35 && y >= 25 && y <= 35));
    const distance = chamfer34(shell, size, size);

    expect(fillHoles(shell, size, size, 4, null, distance)[30 * size + 30]).toBe(0);
    expect(fillHoles(shell, size, size, 8, null, distance)[30 * size + 30]).toBe(1);
  });

  it('skips a hole that contains a single protected pixel (I2)', () => {
    const size = 40;
    const ring = grid(size, size, (x, y) => x >= 6 && x <= 24 && y >= 6 && y <= 24 && !(x >= 14 && x <= 16 && y >= 14 && y <= 16));
    const distance = chamfer34(ring, size, size);
    const protect = new Uint8Array(size * size);
    protect[15 * size + 15] = 1;

    const filled = fillHoles(ring, size, size, 3, protect, distance);
    expect(filled[15 * size + 15]).toBe(0);
    expect(filled[14 * size + 14]).toBe(0);
  });

  it('is idempotent', () => {
    const size = 40;
    const ring = grid(size, size, (x, y) => x >= 6 && x <= 24 && y >= 6 && y <= 24 && !(x >= 14 && x <= 16 && y >= 14 && y <= 16));
    const once = fillHoles(ring, size, size, 3, null, chamfer34(ring, size, size));
    const twice = fillHoles(once, size, size, 3, null, chamfer34(once, size, size));
    expect(Array.from(twice)).toEqual(Array.from(once));
  });
});

describe('growWithin', () => {
  it('moves a straight edge outward by the radius, within a pixel', () => {
    const size = 40;
    const bar = grid(size, size, (x, y) => x >= 10 && x < 20 && y >= 10 && y < 30);
    const distance = chamfer34(bar, size, size);

    const grown = growWithin(bar, distance, 3);
    expect(grown[20 * size + 22]).toBe(1);
    expect(grown[20 * size + 24]).toBe(0);
  });
});

describe('composeRegion', () => {
  const params: MaskRegionParams = {
    close: 2,
    borderSnap: 4,
    holeRadius: 4,
    grow: 0,
    edgeSnap: false,
    edgeRange: 8
  };

  it('returns only what it added, never touching the marks or the protection set', () => {
    const size = 48;
    // A solid square with a one-pixel slit down the middle: closing it is the
    // only thing the pipeline has to do here.
    const a = grid(size, size, (x, y) => x >= 10 && x < 20 && y >= 10 && y < 20 && x !== 15);
    const p = new Uint8Array(size * size);
    p[12 * size + 12] = 1;

    const result = composeRegion({ width: size, height: size, a, p, params });
    expect(result.count).toBe(markedCount(result.additions));
    expect(result.count).toBeGreaterThan(0);
    expect(result.additions[15 * size + 15]).toBe(1);
    for (let index = 0; index < a.length; index += 1) {
      if (a[index]) expect(result.additions[index]).toBe(0);
      if (p[index]) expect(result.additions[index]).toBe(0);
    }
    expect(result.bbox).not.toBeNull();
    expect(result.bbox?.width).toBeLessThan(size);
  });

  it('bridges the seam between two strokes and reports it as an addition', () => {
    const size = 64;
    const a = grid(size, size, (x, y) => y >= 12 && y < 52 && ((x >= 20 && x < 23) || (x >= 26 && x < 29)));
    const result = composeRegion({ width: size, height: size, a, p: null, params });
    const bbox = result.bbox;
    if (!bbox) throw new Error('expected additions');
    expect(result.additions[32 * size + 24]).toBe(1);
  });

  it('is a no-op when everything it would add is protected', () => {
    const size = 64;
    const a = grid(size, size, (x, y) => y >= 12 && y < 52 && ((x >= 20 && x < 23) || (x >= 26 && x < 29)));
    const p = new Uint8Array(size * size);
    filledRect(p, size, { x: 23, y: 12, width: 3, height: 40 });

    const result = composeRegion({ width: size, height: size, a, p, params });
    // The seam itself is protected, so closing cannot fill it; whatever else
    // is added must still avoid P entirely.
    for (let index = 0; index < p.length; index += 1) {
      if (p[index]) expect(result.additions[index]).toBe(0);
    }
    expect(result.additions[32 * size + 24]).toBe(0);
  });

  it('runs the idempotent three twice without changing the answer', () => {
    const size = 64;
    // A seam and an enclosed pinhole, both well away from the frame: closing
    // and hole filling are fixed points, so the second pass has nothing to do.
    const a = grid(
      size,
      size,
      (x, y) =>
        (y >= 12 && y < 52 && x >= 20 && x < 23) ||
        (y >= 12 && y < 52 && x >= 26 && x < 29) ||
        (x >= 40 && x <= 50 && y >= 12 && y <= 22 && !(x >= 44 && x <= 46 && y >= 16 && y <= 18))
    );
    const first = composeRegion({ width: size, height: size, a, p: null, params });
    expect(first.count).toBeGreaterThan(0);
    const once = new Uint8Array(a.length);
    for (let index = 0; index < a.length; index += 1) once[index] = a[index] || first.additions[index];

    const second = composeRegion({ width: size, height: size, a: once, p: null, params });
    expect(second.count).toBe(0);
  });
});

describe('cropRegion', () => {
  it('copies exactly the requested rect, row by row', () => {
    const width = 8;
    const source = new Uint8Array(width * 6);
    source[2 * width + 3] = 1;
    source[3 * width + 4] = 1;
    const cropped = cropRegion(source, width, { x: 3, y: 2, width: 2, height: 2 });
    expect(Array.from(cropped)).toEqual([1, 0, 0, 1]);
  });
});

/**
 * The band-watershed scenarios from the plan's appendix A.1 [8], at 200²: a
 * region painted 20px short of a circular object edge at r = 84, with a 32px
 * band and the edge map's own texture as the only other signal.
 *
 * The geometry is the bench's (480 → 500 with a band of 32) rather than a
 * proportional reduction: the distance prior is a per-pixel gradient of
 * `48 / band`, so squeezing the band changes which side wins the flood.
 */
function watershedScenario(edgeStrength: number, texture: number) {
  const size = 200;
  const center = 100;
  const painted = 64;
  const objectEdge = 84;
  const magnitude = new Uint8Array(size * size);
  const a = new Uint8Array(size * size);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const radius = Math.hypot(x - center, y - center);
      magnitude[y * size + x] =
        Math.abs(radius - objectEdge) < 2 ? edgeStrength : (x * 7 + y * 13) % texture;
      if (radius < painted) a[y * size + x] = 1;
    }
  }
  const edge: EdgeMap = {
    width: size,
    height: size,
    scale: 1,
    sourceWidth: size,
    sourceHeight: size,
    magnitude
  };
  return { size, center, painted, objectEdge, a, edge };
}

/** Furthest radius the watershed won along the +x axis. */
function watershedReach(won: Uint8Array, size: number, center: number, from: number): number {
  let reach = from;
  for (let x = center; x < size; x += 1) {
    if (won[center * size + x]) reach = x - center;
  }
  return reach;
}

describe('bandWatershed', () => {
  it('follows a clear object edge', () => {
    const { size, center, painted, objectEdge, a, edge } = watershedScenario(200, 23);
    const won = bandWatershed(a, null, size, size, edge, 32);
    const reach = watershedReach(won, size, center, painted);

    expect(Math.abs(reach - objectEdge)).toBeLessThanOrEqual(1);
  });

  it('stops early when there is no edge at all', () => {
    const { size, center, painted, a, edge } = watershedScenario(0, 23);
    const won = bandWatershed(a, null, size, size, edge, 32);
    // Without a boundary to follow, only the noise in the texture lets the
    // flood through at all — the bench measured 3px of the 20px gap.
    expect(watershedReach(won, size, center, painted) - painted).toBeLessThanOrEqual(3);
  });

  it('still finds a weak edge under strong texture', () => {
    const { size, center, painted, objectEdge, a, edge } = watershedScenario(60, 81);
    const won = bandWatershed(a, null, size, size, edge, 32);
    const reach = watershedReach(won, size, center, painted);

    expect(reach).toBeGreaterThanOrEqual(objectEdge - 2);
    expect(reach).toBeLessThanOrEqual(objectEdge + 2);
  });

  it('never crosses a protected ring and never exceeds the reach', () => {
    const { size, center, painted, a, edge } = watershedScenario(200, 23);
    const protect = new Uint8Array(size * size);
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        // A protected ring just inside the object edge: the flood may not
        // cross it, whatever the edge map says beyond it.
        const radius = Math.hypot(x - center, y - center);
        if (radius >= 78 && radius < 79) protect[y * size + x] = 1;
      }
    }
    const won = bandWatershed(a, protect, size, size, edge, 32);
    const reach = watershedReach(won, size, center, painted);
    expect(reach).toBeLessThanOrEqual(78);
    for (let index = 0; index < protect.length; index += 1) {
      if (protect[index]) expect(won[index]).toBe(0);
    }
  });

  it('is bounded by the reach when the band is wide', () => {
    const { size, center, painted, a, edge } = watershedScenario(200, 23);
    const won = bandWatershed(a, null, size, size, edge, 8);
    expect(watershedReach(won, size, center, painted) - painted).toBeLessThanOrEqual(8);
  });
});
