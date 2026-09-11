import { describe, expect, it } from 'vitest';
import { buildOffsets, computeRenderWindow, computeSpacers } from '$lib/virtualization/window';

const GAP = 12;
const ESTIMATED = 100;
const OVERSCAN = 200;

function uniformOffsets(count: number, height = ESTIMATED) {
  return buildOffsets(
    Array.from({ length: count }, (_, index) => `row-${index}`),
    {},
    height,
    GAP
  );
}

describe('buildOffsets', () => {
  it('accumulates measured and estimated heights with gaps between rows', () => {
    const offsets = buildOffsets(['a', 'b', 'c'], { b: 50 }, 100, GAP);
    expect(offsets).toEqual([0, 112, 174, 274]);
  });

  it('produces a single zero offset for an empty list', () => {
    expect(buildOffsets([], {}, 100, GAP)).toEqual([0]);
  });
});

describe('computeRenderWindow', () => {
  it('returns an empty window for an empty list', () => {
    expect(computeRenderWindow([0], 0, 0, 500, ESTIMATED, OVERSCAN)).toEqual({ start: 0, end: 0 });
  });

  it('renders from the top when scrolled to the start', () => {
    const window = computeRenderWindow(uniformOffsets(100), 100, 0, 500, ESTIMATED, OVERSCAN);
    expect(window.start).toBe(0);
    expect(window.end).toBeGreaterThanOrEqual(6);
    expect(window.end).toBeLessThan(15);
  });

  it('locates a deep window without scanning every earlier row', () => {
    const offsets = uniformOffsets(10_000);
    const scrollTop = 500 * 112;
    const window = computeRenderWindow(offsets, 10_000, scrollTop, 500, ESTIMATED, OVERSCAN);
    expect(window.start).toBeGreaterThan(480);
    expect(window.start).toBeLessThan(505);
    expect(window.end).toBeGreaterThan(window.start);
  });

  it('matches a linear scan reference for variable heights', () => {
    const measured = Object.fromEntries(
      Array.from({ length: 200 }, (_, index) => [`row-${index}`, 40 + (index % 5) * 30])
    );
    const ids = Array.from({ length: 200 }, (_, index) => `row-${index}`);
    const offsets = buildOffsets(ids, measured, ESTIMATED, GAP);

    for (const scrollTop of [0, 250, 1000, 4000, 39999, 999999]) {
      const rangeStart = Math.max(0, scrollTop - OVERSCAN);
      const rangeEnd = scrollTop + Math.max(300, ESTIMATED) + OVERSCAN;
      let start = 0;
      while (start < ids.length - 1 && offsets[start + 1] < rangeStart) start += 1;
      let end = start + 1;
      while (end < ids.length && offsets[end] < rangeEnd) end += 1;
      const expected = { start, end: Math.min(ids.length, end + 1) };
      expect(computeRenderWindow(offsets, ids.length, scrollTop, 300, ESTIMATED, OVERSCAN)).toEqual(expected);
    }
  });
});

describe('computeSpacers', () => {
  it('preserves total scroll height', () => {
    const offsets = uniformOffsets(100);
    const window = computeRenderWindow(offsets, 100, 2000, 500, ESTIMATED, OVERSCAN);
    const spacers = computeSpacers(offsets, 100, window, GAP);
    const rendered = offsets[window.end] - offsets[window.start] - (window.end < 100 ? GAP : 0);
    expect(spacers.top + rendered + spacers.bottom).toBe(offsets[100]);
  });
});
