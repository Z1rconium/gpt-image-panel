import { describe, expect, it } from 'vitest';
import {
  bandPastThreshold,
  bandRect,
  idsForBand,
  rectsIntersect,
  swipeSelectBegin,
  swipeSelectMove,
  type BandCardEntry
} from '$lib/features/gallery/selectionGestures';

describe('selection band geometry', () => {
  it('normalises the band rect regardless of drag direction', () => {
    expect(bandRect({ x: 40, y: 60 }, { x: 10, y: 20 })).toEqual({ left: 10, top: 20, width: 30, height: 40 });
    expect(bandRect({ x: 10, y: 20 }, { x: 40, y: 60 })).toEqual({ left: 10, top: 20, width: 30, height: 40 });
  });

  it('requires the minimum travel before a press becomes a band', () => {
    expect(bandPastThreshold({ x: 0, y: 0 }, { x: 7, y: 7 })).toBe(false);
    expect(bandPastThreshold({ x: 0, y: 0 }, { x: 8, y: 0 })).toBe(true);
    expect(bandPastThreshold({ x: 0, y: 0 }, { x: 0, y: 8 })).toBe(true);
  });

  it('intersects overlapping rects but not touching edges', () => {
    const a = { left: 0, top: 0, width: 10, height: 10 };
    expect(rectsIntersect(a, { left: 5, top: 5, width: 10, height: 10 })).toBe(true);
    expect(rectsIntersect(a, { left: 10, top: 0, width: 10, height: 10 })).toBe(false);
    expect(rectsIntersect(a, { left: 11, top: 11, width: 5, height: 5 })).toBe(false);
  });

  it('selects only the cards intersecting the band', () => {
    const cards: BandCardEntry[] = [
      { id: 'a', rect: { left: 0, top: 0, width: 10, height: 10 } },
      { id: 'b', rect: { left: 20, top: 0, width: 10, height: 10 } },
      { id: 'c', rect: { left: 40, top: 0, width: 10, height: 10 } }
    ];
    expect(idsForBand({ left: 5, top: 0, width: 20, height: 10 }, cards)).toEqual(['a', 'b']);
    expect(idsForBand({ left: 100, top: 0, width: 5, height: 5 }, cards)).toEqual([]);
  });
});

describe('swipe select gesture', () => {
  it('commits after enough horizontal travel and only once per gesture', () => {
    const state = swipeSelectBegin(1, 0, 0);
    expect(swipeSelectMove(state, 10, 2)).toBe('horizontal');
    expect(swipeSelectMove(state, 30, 5)).toBe('horizontal');
    expect(swipeSelectMove(state, 50, 5)).toBe('committed');
    expect(swipeSelectMove(state, 80, 5)).toBe('idle');
  });

  it('never commits when the drag turns mostly vertical', () => {
    const state = swipeSelectBegin(1, 0, 0);
    expect(swipeSelectMove(state, 60, 40)).toBe('horizontal');
    expect(state.committed).toBe(false);
    expect(swipeSelectMove(state, 60, 120)).toBe('vertical');
    expect(swipeSelectMove(state, 120, 40)).toBe('vertical');
  });

  it('requires horizontal dominance beyond the axis ratio to commit', () => {
    const state = swipeSelectBegin(1, 0, 0);
    expect(swipeSelectMove(state, 100, 51)).toBe('horizontal');
    expect(state.committed).toBe(false);
    expect(swipeSelectMove(state, 100, 120)).toBe('vertical');
    expect(state.committed).toBe(false);
  });
});
