import { describe, expect, it } from 'vitest';
import {
  DirtyRegionList,
  MaskCheckpointer,
  MaskCoverageTracker,
  MaskImportError,
  MaskUndoPatches,
  binarizeMaskAlphaData,
  countMarked,
  countMarkedPixels,
  expandRect,
  normalizeSmoothPx,
  pngHasAlphaChannel,
  readPngSize,
  rectArea,
  rectCoversAll,
  shapeRectFor,
  strokeRectFor,
  unionRect,
  type MaskRect
} from '$lib/features/mask/maskDocument';

function pngHeader(colorType: number, extra: number[] = []): Uint8Array {
  const bytes = new Uint8Array(33 + extra.length);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  bytes[25] = colorType;
  if (extra.length) bytes.set(extra, 33);
  return bytes;
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

describe('countMarked', () => {
  it('counts marked pixels absolutely', () => {
    const data = new Uint8ClampedArray([0, 0, 0, 0, 0, 0, 0, 128, 0, 0, 0, 127, 0, 0, 0, 255]);
    expect(countMarked(data)).toBe(2);
    expect(countMarked(new Uint8ClampedArray(0))).toBe(0);
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

function pngHeaderWithSize(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  return bytes;
}

describe('readPngSize', () => {
  it('reads width/height straight from the IHDR chunk, no decode needed', () => {
    expect(readPngSize(pngHeaderWithSize(4096, 2048))).toEqual({ width: 4096, height: 2048 });
    expect(readPngSize(pngHeaderWithSize(1, 1))).toEqual({ width: 1, height: 1 });
  });

  it('rejects data that is not a PNG or too short to hold an IHDR', () => {
    expect(readPngSize(new Uint8Array(23))).toBeNull();
    expect(readPngSize(new Uint8Array([1, 2, 3]))).toBeNull();
  });

  it('rejects a degenerate zero-sized IHDR', () => {
    expect(readPngSize(pngHeaderWithSize(0, 100))).toBeNull();
    expect(readPngSize(pngHeaderWithSize(100, 0))).toBeNull();
  });
});

describe('normalizeSmoothPx', () => {
  it('rounds valid radii and rejects non-positive or non-finite input', () => {
    expect(normalizeSmoothPx(9.4, 1024)).toBe(9);
    expect(normalizeSmoothPx(0, 1024)).toBe(0);
    expect(normalizeSmoothPx(-8, 1024)).toBe(0);
    expect(normalizeSmoothPx(Number.NaN, 1024)).toBe(0);
  });

  it('caps the radius at the maximum and at an eighth of the image width', () => {
    expect(normalizeSmoothPx(500, 1024)).toBe(64);
    expect(normalizeSmoothPx(64, 64)).toBe(8);
    expect(normalizeSmoothPx(64, 16)).toBe(2);
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

describe('processed-view rect helpers', () => {
  it('expandRect pads on every side and clamps to the canvas', () => {
    expect(expandRect({ x: 10, y: 20, width: 30, height: 40 }, 4, 100, 100)).toEqual({
      x: 6,
      y: 16,
      width: 38,
      height: 48
    });
    expect(expandRect({ x: 0, y: 0, width: 10, height: 10 }, 6, 100, 100)).toEqual({
      x: 0,
      y: 0,
      width: 16,
      height: 16
    });
  });

  it('expandRect returns null when the padded rect lands outside the canvas', () => {
    expect(expandRect({ x: 0, y: 0, width: 0, height: 0 }, 0, 100, 100)).toBeNull();
  });

  it('rectArea and rectCoversAll describe the repaint gate', () => {
    expect(rectArea({ x: 0, y: 0, width: 40, height: 30 })).toBe(1200);
    expect(rectArea({ x: 0, y: 0, width: -5, height: 30 })).toBe(0);

    expect(rectCoversAll(null, 100, 100)).toBe(true);
    expect(rectCoversAll({ x: 0, y: 0, width: 100, height: 100 }, 100, 100)).toBe(true);
    expect(rectCoversAll({ x: 0, y: 0, width: 60, height: 100 }, 100, 100)).toBe(false);
    expect(rectCoversAll({ x: 1, y: 0, width: 100, height: 100 }, 100, 100)).toBe(false);
  });
});

describe('unionRect', () => {
  it('unions overlapping rectangles', () => {
    expect(unionRect({ x: 0, y: 0, width: 10, height: 10 }, { x: 5, y: 5, width: 20, height: 2 })).toEqual({
      x: 0,
      y: 0,
      width: 25,
      height: 10
    });
  });
});

describe('strokeRectFor', () => {
  it('covers every pixel a stroke of the given size can paint', () => {
    const width = 128;
    const height = 96;
    const points = [
      { x: 10, y: 10 },
      { x: 80, y: 12 },
      { x: 60, y: 70 },
      { x: 8, y: 80 }
    ];
    for (const size of [1, 5, 32, 500]) {
      const rect = strokeRectFor(points, size, width, height);
      if (!rect) throw new Error('expected a rect');
      const radius = Math.max(1, size) / 2;
      for (const point of points) {
        for (let y = Math.floor(point.y - radius - 1); y <= Math.ceil(point.y + radius + 1); y += 1) {
          for (let x = Math.floor(point.x - radius - 1); x <= Math.ceil(point.x + radius + 1); x += 1) {
            // Pixels outside the canvas can never be painted; the rect may
            // legitimately clamp them away.
            if (x < 0 || y < 0 || x >= width || y >= height) continue;
            if ((x - point.x) ** 2 + (y - point.y) ** 2 > radius ** 2) continue;
            expect(x).toBeGreaterThanOrEqual(rect.x);
            expect(x).toBeLessThan(rect.x + rect.width);
            expect(y).toBeGreaterThanOrEqual(rect.y);
            expect(y).toBeLessThan(rect.y + rect.height);
          }
        }
      }
    }
  });

  it('clamps to the canvas and rejects empty point lists', () => {
    expect(strokeRectFor([], 8, 64, 64)).toBeNull();
    // A stroke completely outside the canvas clamps to an empty rect.
    expect(strokeRectFor([{ x: -20, y: -20 }], 8, 64, 64)).toBeNull();
    const rect = strokeRectFor([{ x: 0, y: 0 }], 8, 64, 64);
    expect(rect).toEqual({ x: 0, y: 0, width: 6, height: 6 });
  });
});

describe('stroke segment rects', () => {
  it('spans the point a segment is drawn from, not just the new points', () => {
    // drawStroke() starts its path at points[fromIndex - 1], so a segment's box
    // has to include that point: otherwise a pointer jump longer than the brush
    // leaves the stretch between them out of the count and the dirty region.
    const previous = { x: 20, y: 100 };
    const next = { x: 180, y: 100 };
    const rect = strokeRectFor([previous, next], 8, 200, 200);
    if (!rect) throw new Error('expected a rect');

    expect(rect.x).toBeLessThanOrEqual(previous.x - 6);
    expect(rect.x + rect.width).toBeGreaterThanOrEqual(next.x + 6);
    // The omitted-point bug shows up as a box that starts at the *new* point.
    const newPointsOnly = strokeRectFor([next], 8, 200, 200);
    expect(rect.width).toBeGreaterThan(newPointsOnly?.width ?? 0);
  });
});

describe('shapeRectFor', () => {
  it('bounds shape outlines with a small antialias margin', () => {
    expect(shapeRectFor([{ x: 10, y: 10 }, { x: 40, y: 30 }], 64, 64)).toEqual({
      x: 8,
      y: 8,
      width: 34,
      height: 24
    });
    expect(shapeRectFor([], 64, 64)).toBeNull();
  });
});

/** Minimal software rasterizer standing in for the marks canvas in tests. */
class MaskBuffer {
  readonly width: number;
  readonly height: number;
  private readonly data: Uint8ClampedArray;

  constructor(width: number, height: number) {
    this.width = width;
    this.height = height;
    this.data = new Uint8ClampedArray(width * height * 4);
  }

  countRegion = (rect: MaskRect): number => {
    const x = Math.max(0, Math.min(this.width, Math.floor(rect.x)));
    const y = Math.max(0, Math.min(this.height, Math.floor(rect.y)));
    const right = Math.max(x, Math.min(this.width, Math.ceil(rect.x + rect.width)));
    const bottom = Math.max(y, Math.min(this.height, Math.ceil(rect.y + rect.height)));
    let marked = 0;
    for (let row = y; row < bottom; row += 1) {
      let index = (row * this.width + x) * 4 + 3;
      for (let column = x; column < right; column += 1, index += 4) {
        if (this.data[index] >= 128) marked += 1;
      }
    }
    return marked;
  };

  markedCount(): number {
    return this.countRegion({ x: 0, y: 0, width: this.width, height: this.height });
  }

  /**
   * Fold a mutation into the tracker the way the document does: count the
   * affected region before and after, then hand the pair to `applyDelta`.
   */
  apply(tracker: MaskCoverageTracker, rect: MaskRect | null, mutate: () => void) {
    const before = rect ? this.countRegion(rect) : 0;
    mutate();
    if (rect) tracker.applyDelta(before, this.countRegion(rect));
  }

  stampDisc(centerX: number, centerY: number, radius: number, erase: boolean) {
    const r2 = radius * radius;
    for (let y = Math.floor(centerY - radius); y <= Math.ceil(centerY + radius); y += 1) {
      for (let x = Math.floor(centerX - radius); x <= Math.ceil(centerX + radius); x += 1) {
        if (x < 0 || y < 0 || x >= this.width || y >= this.height) continue;
        if ((x - centerX) ** 2 + (y - centerY) ** 2 > r2) continue;
        this.data[(y * this.width + x) * 4 + 3] = erase ? 0 : 255;
      }
    }
  }

  fillRectRegion(rect: MaskRect, erase: boolean) {
    for (let y = rect.y; y < rect.y + rect.height; y += 1) {
      for (let x = rect.x; x < rect.x + rect.width; x += 1) {
        this.data[(y * this.width + x) * 4 + 3] = erase ? 0 : 255;
      }
    }
  }

  invertAll() {
    for (let index = 3; index < this.data.length; index += 4) {
      this.data[index] = 255 - this.data[index];
    }
  }

  clearAll() {
    this.data.fill(0);
  }
}

describe('MaskCoverageTracker', () => {
  it('matches full recounts across random strokes, shapes, invert and clear', () => {
    const width = 96;
    const height = 64;
    const total = width * height;
    const buffer = new MaskBuffer(width, height);
    const tracker = new MaskCoverageTracker(width, height);
    const expectConsistent = () => {
      expect(Math.round(tracker.coverage() * total)).toBe(buffer.markedCount());
    };

    const random = mulberry32(20260921);
    const point = () => ({ x: Math.floor(random() * width), y: Math.floor(random() * height) });

    for (let step = 0; step < 40; step += 1) {
      const erase = random() < 0.3;
      if (random() < 0.2) {
        const shape = [point(), point()];
        const rect = shapeRectFor(shape, width, height);
        buffer.apply(tracker, rect, () => {
          if (rect) buffer.fillRectRegion(rect, erase);
        });
      } else {
        const size = 2 + Math.floor(random() * 18);
        const radius = Math.max(1, size) / 2;
        const first = point();
        buffer.apply(tracker, strokeRectFor([first], size, width, height), () =>
          buffer.stampDisc(first.x, first.y, radius, erase)
        );
        const extra = 1 + Math.floor(random() * 5);
        let previous = first;
        for (let index = 0; index < extra; index += 1) {
          const next = point();
          // Segments are reported with the point they are drawn from, exactly
          // like the document's extendStroke (P2).
          const rect = strokeRectFor([previous, next], size, width, height);
          buffer.apply(tracker, rect, () => buffer.stampDisc(next.x, next.y, radius, erase));
          previous = next;
        }
      }
      expectConsistent();
      if (random() < 0.15) {
        tracker.invert();
        buffer.invertAll();
        expectConsistent();
      }
      if (random() < 0.1) {
        tracker.clear();
        buffer.clearAll();
        expectConsistent();
      }
    }
  });

  it('applyDelta moves the total by the region difference, including downwards', () => {
    const buffer = new MaskBuffer(32, 32);
    const tracker = new MaskCoverageTracker(32, 32);
    const rect = strokeRectFor([{ x: 8, y: 8 }], 6, 32, 32);
    if (!rect) throw new Error('expected a rect');

    // A paint inside the region raises the total...
    buffer.apply(tracker, rect, () => buffer.stampDisc(8, 8, 3, false));
    expect(Math.round(tracker.coverage() * 32 * 32)).toBe(buffer.markedCount());
    expect(tracker.markedCount()).toBeGreaterThan(0);

    // ...and an erase over it brings the total back down with it.
    const marked = tracker.markedCount();
    buffer.apply(tracker, rect, () => buffer.stampDisc(8, 8, 3, true));
    expect(tracker.markedCount()).toBeLessThan(marked);
    expect(tracker.markedCount()).toBe(buffer.markedCount());
  });

  it('a segment the mirror already knows the contents of reports its own delta', () => {
    const buffer = new MaskBuffer(32, 32);
    const tracker = new MaskCoverageTracker(32, 32);
    const rect = strokeRectFor([{ x: 8, y: 8 }], 6, 32, 32);
    if (!rect) throw new Error('expected a rect');

    buffer.stampDisc(8, 8, 3, false);
    tracker.resetTo(buffer.countRegion(rect));

    // Repainting the same pixels is a no-op, exactly what folding before/after
    // counts into the running total has to produce.
    buffer.apply(tracker, rect, () => buffer.stampDisc(8, 8, 3, false));
    expect(Math.round(tracker.coverage() * 32 * 32)).toBe(buffer.markedCount());
  });

  it('reports zero coverage for an empty document', () => {
    expect(new MaskCoverageTracker(0, 0).coverage()).toBe(0);
  });
});

describe('MaskCheckpointer', () => {
  it('records a snapshot only at each interval, and only once per length', () => {
    const checkpointer = new MaskCheckpointer<number>(8, 4);
    let snapshots = 0;
    const snapshot = () => {
      snapshots += 1;
      return snapshots;
    };

    for (let length = 1; length <= 7; length += 1) checkpointer.record(length, snapshot);
    expect(snapshots).toBe(0);

    checkpointer.record(8, snapshot);
    expect(snapshots).toBe(1);
    // Re-recording the same length (e.g. a caller invoking record() twice for
    // one settled command) must not snapshot again.
    checkpointer.record(8, snapshot);
    expect(snapshots).toBe(1);

    checkpointer.record(16, snapshot);
    expect(snapshots).toBe(2);
    expect(checkpointer.nearestAtOrBelow(16)?.afterIndex).toBe(16);
    expect(checkpointer.nearestAtOrBelow(20)?.afterIndex).toBe(16);
    expect(checkpointer.nearestAtOrBelow(15)?.afterIndex).toBe(8);
    expect(checkpointer.nearestAtOrBelow(7)).toBeNull();
  });

  it('evicts the oldest checkpoint once past the cap', () => {
    const checkpointer = new MaskCheckpointer<number>(8, 2);
    for (const length of [8, 16, 24]) checkpointer.record(length, () => length);

    // Query highest-to-lowest: nearestAtOrBelow() prunes anything past its
    // own argument, so checking 24 then 16 first avoids that pruning masking
    // whether 8 was evicted by the cap versus by the queries themselves.
    expect(checkpointer.nearestAtOrBelow(24)?.afterIndex).toBe(24);
    expect(checkpointer.nearestAtOrBelow(16)?.afterIndex).toBe(16);
    // Cap is 2: the checkpoint at 8 should have been evicted already, not
    // just pruned by the queries above (which only ever remove entries
    // *past* their argument, never at or below it).
    expect(checkpointer.nearestAtOrBelow(8)).toBeNull();
  });

  it('prunes checkpoints past an undo target and keeps shallower ones usable', () => {
    const checkpointer = new MaskCheckpointer<number>(8, 4);
    for (const length of [8, 16, 24, 32]) checkpointer.record(length, () => length);

    // Undoing down to 20 commands invalidates the 24/32 checkpoints; the
    // shallower ones remain valid jump points for the next few undos.
    expect(checkpointer.nearestAtOrBelow(20)?.afterIndex).toBe(16);
    // A fresh checkpoint at the same length as a pruned one (e.g. painting
    // again after the undo) must not resurrect the stale snapshot.
    checkpointer.record(24, () => 240);
    expect(checkpointer.nearestAtOrBelow(24)?.snapshot).toBe(240);
  });

  it('clear() drops every checkpoint', () => {
    const checkpointer = new MaskCheckpointer<number>(8, 4);
    checkpointer.record(8, () => 8);
    checkpointer.clear();
    expect(checkpointer.nearestAtOrBelow(8)).toBeNull();
  });
});

describe('MaskUndoPatches', () => {
  it('retains a patch until the byte budget forces it out', () => {
    const patches = new MaskUndoPatches<string>(100);
    patches.record(1, 40, 'a');
    patches.record(2, 40, 'b');
    expect(patches.usedBytes).toBe(80);
    expect(patches.get(1)).toBe('a');
    expect(patches.get(2)).toBe('b');

    // The third patch pushes the total past 100 bytes; the oldest (afterIndex
    // 1) is evicted first, since undo works from the top of the stack down.
    patches.record(3, 40, 'c');
    expect(patches.usedBytes).toBe(80);
    expect(patches.get(1)).toBeNull();
    expect(patches.get(2)).toBe('b');
    expect(patches.get(3)).toBe('c');
  });

  it('evicts as many oldest entries as needed for one large patch', () => {
    const patches = new MaskUndoPatches<string>(100);
    patches.record(1, 30, 'a');
    patches.record(2, 30, 'b');
    patches.record(3, 90, 'c');
    expect(patches.get(1)).toBeNull();
    expect(patches.get(2)).toBeNull();
    expect(patches.get(3)).toBe('c');
    expect(patches.usedBytes).toBe(90);
  });

  it('a patch bigger than the whole budget is dropped rather than kept over budget', () => {
    const patches = new MaskUndoPatches<string>(50);
    patches.record(1, 500, 'huge');
    expect(patches.get(1)).toBeNull();
    expect(patches.usedBytes).toBe(0);
  });

  it('dropAbove removes only patches past the given index, like a cleared redo stack', () => {
    const patches = new MaskUndoPatches<number>(1000);
    for (const index of [1, 2, 3, 4]) patches.record(index, 10, index * 10);

    patches.dropAbove(2);
    expect(patches.get(1)).toBe(10);
    expect(patches.get(2)).toBe(20);
    expect(patches.get(3)).toBeNull();
    expect(patches.get(4)).toBeNull();
    expect(patches.usedBytes).toBe(20);

    // A fresh patch landing at a dropped index must not resurrect the old one.
    patches.record(3, 10, 999);
    expect(patches.get(3)).toBe(999);
  });

  it('get() returns null for an index that was never recorded', () => {
    const patches = new MaskUndoPatches<number>(1000);
    expect(patches.get(5)).toBeNull();
  });

  it('clear() drops every patch and resets the byte total', () => {
    const patches = new MaskUndoPatches<number>(1000);
    patches.record(1, 100, 1);
    patches.clear();
    expect(patches.get(1)).toBeNull();
    expect(patches.usedBytes).toBe(0);
  });
});

describe('DirtyRegionList', () => {
  it('merges rects that land close together and keeps far-apart ones separate', () => {
    const dirty = new DirtyRegionList(32, 1.5);
    dirty.markRect({ x: 0, y: 0, width: 10, height: 10 });
    dirty.markRect({ x: 3, y: 3, width: 10, height: 10 });
    // These two overlap heavily, so the union wastes little area and they merge.
    expect(dirty.list).toHaveLength(1);

    dirty.markRect({ x: 1000, y: 1000, width: 10, height: 10 });
    // Far away: unioning would waste far more than mergeWaste allows, so it
    // stays a separate region instead of ballooning the dirty area.
    expect(dirty.list).toHaveLength(2);
  });

  it('degrades to a single union once past the region cap', () => {
    const dirty = new DirtyRegionList(3, 1.01);
    // Far enough apart, and with a tight merge tolerance, that none of these
    // merge with each other on their own.
    for (let index = 0; index < 3; index += 1) {
      dirty.markRect({ x: index * 1000, y: index * 1000, width: 1, height: 1 });
    }
    expect(dirty.list).toHaveLength(3);

    // The 4th distinct rect pushes the list past the cap, so it collapses to
    // one bounding union instead of growing further.
    dirty.markRect({ x: 3000, y: 3000, width: 1, height: 1 });
    expect(dirty.list).toHaveLength(1);
    expect(dirty.isFull).toBe(false);
  });

  it('markFull short-circuits further rects until clear()', () => {
    const dirty = new DirtyRegionList();
    dirty.markRect({ x: 0, y: 0, width: 5, height: 5 });
    dirty.markFull();
    expect(dirty.isFull).toBe(true);
    expect(dirty.list).toEqual([]);

    dirty.markRect({ x: 100, y: 100, width: 5, height: 5 });
    expect(dirty.isFull).toBe(true);

    dirty.clear();
    expect(dirty.isFull).toBe(false);
    expect(dirty.isEmpty).toBe(true);
  });

  it('isEmpty is true only with no regions and not full', () => {
    const dirty = new DirtyRegionList();
    expect(dirty.isEmpty).toBe(true);
    dirty.markRect({ x: 0, y: 0, width: 1, height: 1 });
    expect(dirty.isEmpty).toBe(false);
  });
});
