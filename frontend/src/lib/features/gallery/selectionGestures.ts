/**
 * Pure geometry/state helpers behind the gallery drag-band and swipe-select
 * gestures, kept framework-free so the math can be unit tested directly.
 */

export type Point = { x: number; y: number };

export type BandRect = { left: number; top: number; width: number; height: number };

/** Minimum pointer travel before a press turns into a selection band. */
export const BAND_START_THRESHOLD_PX = 8;
/** Horizontal travel required for a card swipe to toggle selection. */
export const SWIPE_SELECT_THRESHOLD_PX = 48;
/** Horizontal travel must exceed vertical travel by this factor... */
export const SWIPE_SELECT_AXIS_RATIO = 2;

export function bandRect(start: Point, current: Point): BandRect {
  return {
    left: Math.min(start.x, current.x),
    top: Math.min(start.y, current.y),
    width: Math.abs(current.x - start.x),
    height: Math.abs(current.y - start.y)
  };
}

export function bandPastThreshold(start: Point, current: Point, threshold = BAND_START_THRESHOLD_PX): boolean {
  return Math.abs(current.x - start.x) >= threshold || Math.abs(current.y - start.y) >= threshold;
}

export function rectsIntersect(a: BandRect, b: BandRect): boolean {
  return (
    a.left < b.left + b.width &&
    a.left + a.width > b.left &&
    a.top < b.top + b.height &&
    a.top + a.height > b.top
  );
}

export type BandCardEntry = { id: string; rect: BandRect };

export function idsForBand(band: BandRect, cards: BandCardEntry[]): string[] {
  return cards.filter((card) => rectsIntersect(band, card.rect)).map((card) => card.id);
}

export type SwipeSelectState = {
  pointerId: number;
  startX: number;
  startY: number;
  committed: boolean;
};

export type SwipeSelectMove = 'idle' | 'horizontal' | 'vertical' | 'committed';

export function swipeSelectBegin(pointerId: number, x: number, y: number): SwipeSelectState {
  return { pointerId, startX: x, startY: y, committed: false };
}

export function swipeSelectMove(state: SwipeSelectState, x: number, y: number): SwipeSelectMove {
  if (state.committed) return 'committed';
  const dx = x - state.startX;
  const dy = y - state.startY;
  // Vertical scroll and system navigation gestures always win: a mostly
  // vertical drag is never treated as a horizontal selection swipe.
  if (Math.abs(dy) > Math.abs(dx)) return 'vertical';
  if (Math.abs(dx) >= SWIPE_SELECT_THRESHOLD_PX && Math.abs(dx) >= Math.abs(dy) * SWIPE_SELECT_AXIS_RATIO) {
    state.committed = true;
    return 'committed';
  }
  return 'horizontal';
}
