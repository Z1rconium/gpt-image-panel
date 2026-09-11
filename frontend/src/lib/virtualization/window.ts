export type VirtualWindow = {
  start: number;
  end: number;
};

/**
 * Builds cumulative row offsets (length = itemCount + 1) from measured row
 * heights, falling back to `estimatedHeight`. Offsets are non-decreasing, which
 * is what lets computeRenderWindow() binary-search the visible range.
 */
export function buildOffsets(
  itemIds: readonly string[],
  measuredHeights: Record<string, number>,
  estimatedHeight: number,
  gap: number
): number[] {
  const offsets = new Array<number>(itemIds.length + 1);
  offsets[0] = 0;
  for (let index = 0; index < itemIds.length; index += 1) {
    const height = measuredHeights[itemIds[index]] || estimatedHeight;
    offsets[index + 1] = offsets[index] + height + (index < itemIds.length - 1 ? gap : 0);
  }
  return offsets;
}

/** First index in [0, offsets.length) whose offset is >= target (offsets sorted ascending). */
function lowerBound(offsets: readonly number[], target: number): number {
  let low = 0;
  let high = offsets.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (offsets[mid] < target) low = mid + 1;
    else high = mid;
  }
  return low;
}

/**
 * The half-open [start, end) row range to render for a given scroll position,
 * found with a binary search instead of a linear scan from row 0.
 */
export function computeRenderWindow(
  offsets: readonly number[],
  itemCount: number,
  scrollTop: number,
  viewportHeight: number,
  estimatedHeight: number,
  overscanPx: number
): VirtualWindow {
  if (!itemCount) return { start: 0, end: 0 };

  const rangeStart = Math.max(0, scrollTop - overscanPx);
  const rangeEnd = scrollTop + Math.max(viewportHeight, estimatedHeight) + overscanPx;

  const start = Math.max(0, Math.min(itemCount - 1, lowerBound(offsets, rangeStart) - 1));
  const endIndex = lowerBound(offsets, rangeEnd);
  const end = Math.min(itemCount, Math.max(start + 1, endIndex + 1));
  return { start, end };
}

export type VirtualSpacers = {
  top: number;
  bottom: number;
};

/** Top/bottom spacer heights that preserve total scroll height around the rendered window. */
export function computeSpacers(
  offsets: readonly number[],
  itemCount: number,
  window: VirtualWindow,
  gap: number
): VirtualSpacers {
  const top = offsets[window.start] || 0;
  const windowBottom = offsets[window.end] || 0;
  const renderedHeight = windowBottom - top - (window.end < itemCount ? gap : 0);
  const totalHeight = offsets[itemCount] || 0;
  return { top, bottom: Math.max(0, totalHeight - top - renderedHeight) };
}
