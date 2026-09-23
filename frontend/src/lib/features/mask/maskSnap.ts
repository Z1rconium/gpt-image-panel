/**
 * Magnetic edge snapping for the mask editor.
 *
 * A downscaled Sobel gradient map of the primary image is built once, lazily,
 * and the lasso/rectangle tools use it to pull their traced points onto the
 * strongest nearby edge — the "magnetic" behaviour that makes tracing a
 * subject's outline freehand practical instead of a pixel hunt.
 *
 * Everything here is pure and canvas-free: the caller rasterizes the primary
 * image into a small buffer (see `MaskEditorDialog.ensureSnapMap`) and this
 * module only ever reads plain arrays, so it can be unit-tested directly.
 */

export type EdgeMap = {
  width: number;
  height: number;
  /** Map pixels per source pixel (nominal; used to convert a radius). */
  scale: number;
  sourceWidth: number;
  sourceHeight: number;
  /** width * height gradient magnitudes, 0..255. */
  magnitude: Uint8Array;
};

/**
 * Long-edge cap for the edge map. A 1600² map is ~2.6 MB and keeps the one-off
 * Sobel pass in the tens of milliseconds even for a 4096² photo.
 */
export const SNAP_MAX_EDGE = 1600;

/**
 * Gradients below this are treated as flat area and left alone, so freehand
 * strokes across smooth regions still follow the cursor exactly.
 */
export const SNAP_MIN_STRENGTH = 48;

/** Snap radius is defined in screen pixels (converted per zoom level). */
export const SNAP_RADIUS_MIN = 4;
export const SNAP_RADIUS_MAX = 40;
export const SNAP_RADIUS_DEFAULT = 14;

/**
 * How much a candidate edge is penalized for being far from the cursor. The
 * strongest edge inside the radius still wins over a nearer weak one, but a
 * comparable edge under the cursor is preferred over a distant one.
 */
const DISTANCE_PENALTY = 24;

/** The image frame is always a valid snap target (also lets the rect tool box the picture). */
const BORDER_STRENGTH = 255;

/**
 * Downscaled edge-map dimensions for a source image; only ever shrinks. The
 * scale is applied to both axes, so the map keeps the source aspect ratio
 * (rounded to whole pixels).
 */
export function edgeMapSize(
  width: number,
  height: number,
  maxEdge: number = SNAP_MAX_EDGE
): { width: number; height: number; scale: number } {
  const longest = Math.max(width, height);
  if (!Number.isFinite(longest) || longest <= 0) return { width: 1, height: 1, scale: 1 };
  const limit = Math.max(1, Math.floor(maxEdge));
  const scale = longest > limit ? limit / longest : 1;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
    scale
  };
}

/**
 * Grayscale + Sobel gradient magnitude of an RGBA buffer.
 *
 * `data` must already be at the map's own dimensions; the caller does the
 * downscale through a canvas. The outermost 1px ring is forced to the maximum
 * so the picture frame itself is a snap target.
 */
export function computeEdgeMap(
  data: Uint8ClampedArray,
  width: number,
  height: number,
  options: { scale?: number; sourceWidth?: number; sourceHeight?: number } = {}
): EdgeMap {
  const magnitude = new Uint8Array(width * height);
  if (width <= 0 || height <= 0 || data.length < width * height * 4) {
    return {
      width: Math.max(1, width),
      height: Math.max(1, height),
      scale: options.scale ?? 1,
      sourceWidth: options.sourceWidth ?? Math.max(1, width),
      sourceHeight: options.sourceHeight ?? Math.max(1, height),
      magnitude
    };
  }

  // Luma weights sum to 256 (77 + 150 + 29), so `>> 8` lands in 0..255.
  const gray = new Uint8ClampedArray(width * height);
  for (let index = 0, pixel = 0; index < gray.length; index += 1, pixel += 4) {
    gray[index] = (data[pixel] * 77 + data[pixel + 1] * 150 + data[pixel + 2] * 29) >> 8;
  }

  for (let y = 1; y < height - 1; y += 1) {
    const rowAbove = (y - 1) * width;
    const row = y * width;
    const rowBelow = (y + 1) * width;
    for (let x = 1; x < width - 1; x += 1) {
      const topLeft = gray[rowAbove + x - 1];
      const top = gray[rowAbove + x];
      const topRight = gray[rowAbove + x + 1];
      const midLeft = gray[row + x - 1];
      const midRight = gray[row + x + 1];
      const bottomLeft = gray[rowBelow + x - 1];
      const bottom = gray[rowBelow + x];
      const bottomRight = gray[rowBelow + x + 1];
      const gx =
        topRight + 2 * midRight + bottomRight - (topLeft + 2 * midLeft + bottomLeft);
      const gy = bottomLeft + 2 * bottom + bottomRight - (topLeft + 2 * top + topRight);
      // A full black/white step yields |gx| = 4 * 255 -> 255 after `>> 2`.
      magnitude[row + x] = Math.min(255, (Math.abs(gx) + Math.abs(gy)) >> 2);
    }
  }

  for (let x = 0; x < width; x += 1) {
    magnitude[x] = BORDER_STRENGTH;
    magnitude[(height - 1) * width + x] = BORDER_STRENGTH;
  }
  for (let y = 0; y < height; y += 1) {
    magnitude[y * width] = BORDER_STRENGTH;
    magnitude[y * width + width - 1] = BORDER_STRENGTH;
  }

  return {
    width,
    height,
    scale: options.scale ?? 1,
    sourceWidth: options.sourceWidth ?? width,
    sourceHeight: options.sourceHeight ?? height,
    magnitude
  };
}

/**
 * Pull `(x, y)` (source-image coordinates) onto the strongest gradient within
 * `radiusImagePx`. Returns the input unchanged with `snapped: false` when the
 * best candidate is below `minStrength` — flat areas stay freehand.
 */
export function snapToEdge(
  map: EdgeMap,
  x: number,
  y: number,
  radiusImagePx: number,
  minStrength: number = SNAP_MIN_STRENGTH
): { x: number; y: number; strength: number; snapped: boolean } {
  const { width, height, sourceWidth, sourceHeight, magnitude } = map;
  const centerX = (x / sourceWidth) * width;
  const centerY = (y / sourceHeight) * height;
  // Use a conservative map-space search box, then enforce the radius in source
  // pixels below. Edge-map rounding can make its X and Y scales differ.
  const scale = Math.max(width / sourceWidth, height / sourceHeight);
  const radius = Math.max(1, Math.ceil(Math.max(0, radiusImagePx) * scale));

  const minX = Math.max(0, Math.floor(centerX) - radius);
  const maxX = Math.min(width - 1, Math.floor(centerX) + radius);
  const minY = Math.max(0, Math.floor(centerY) - radius);
  const maxY = Math.min(height - 1, Math.floor(centerY) + radius);

  const radiusSquared = Math.max(0, radiusImagePx) ** 2;
  let bestScore = -Infinity;
  let bestStrength = 0;
  let bestX = -1;
  let bestY = -1;

  for (let mapY = minY; mapY <= maxY; mapY += 1) {
    const row = mapY * width;
    for (let mapX = minX; mapX <= maxX; mapX += 1) {
      const strength = magnitude[row + mapX];
      if (strength <= 0) continue;
      const dx = ((mapX + 0.5) * sourceWidth) / width - x;
      const dy = ((mapY + 0.5) * sourceHeight) / height - y;
      const distanceSquared = dx * dx + dy * dy;
      if (distanceSquared > radiusSquared) continue;
      const score =
        strength - Math.round((distanceSquared / (radiusSquared + 1)) * DISTANCE_PENALTY);
      if (score > bestScore) {
        bestScore = score;
        bestStrength = strength;
        bestX = mapX;
        bestY = mapY;
      }
    }
  }

  if (bestX < 0 || bestStrength < minStrength) {
    return { x, y, strength: Math.max(0, bestStrength), snapped: false };
  }

  return {
    x: ((bestX + 0.5) * sourceWidth) / width,
    y: ((bestY + 0.5) * sourceHeight) / height,
    strength: bestStrength,
    snapped: true
  };
}
