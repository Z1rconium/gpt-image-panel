/**
 * Region-level gap filling for the mask editor ("空缺自动补全").
 *
 * The upstream contract only edits alpha == 0 pixels, so any pixel inside a
 * selection that was never painted stays untouched there: pinholes between
 * brush strokes, the thin border left when a stroke stops a few pixels short of
 * the frame, antialiased object edges the selection stopped inside of. These
 * passes grow the marked region into a well-defined superset of it.
 *
 * Two invariants drive every algorithm below:
 *
 * - I1 (monotone): the result always contains the input. No pass may drop a
 *   pixel the user painted, so smoothing and gap filling can only add area.
 * - I2 (protected): pixels the user explicitly erased are never restored. The
 *   protection set is threaded through as a binary mirror and excluded from
 *   every addition.
 *
 * Everything here is pure and canvas-free: it works on plain `Uint8Array`
 * bitmaps, so the whole pipeline runs in a worker (see maskRegion.worker.ts)
 * and can be unit-tested without a browser.
 */

import type { MaskRect } from './maskDocument';
import { SNAP_MIN_STRENGTH, type EdgeMap } from './maskSnap';

export type MaskRegionStrength = 'light' | 'normal' | 'strong';

/** What the user picked; every distance below is derived per image size. */
export type MaskRegionPreferences = {
  enabled: boolean;
  strength: MaskRegionStrength;
  edgeSnap: boolean;
  /** Edge-snap reach in image pixels; null keeps the size-derived default. */
  edgeRange: number | null;
  /** Extra dilation in image pixels (0-32). */
  grow: number;
};

/** The resolved numbers one compose pass runs with, all in image pixels. */
export type MaskRegionParams = {
  /** Closing radius: gaps narrower than 2c are joined. */
  close: number;
  /** Border snap reach: marks within k of the frame drag a line to it. */
  borderSnap: number;
  /** Largest enclosed hole (by inradius) that gets filled. */
  holeRadius: number;
  /** Dilation distance applied to the finished region. */
  grow: number;
  edgeSnap: boolean;
  edgeRange: number;
};

export type MaskRegionRequest = {
  version: number;
  width: number;
  height: number;
  /** Binary marks mirror: 1 = the user painted this pixel. */
  a: Uint8Array;
  /** Binary protection mirror: 1 = the user explicitly erased this pixel. */
  p: Uint8Array | null;
  params: MaskRegionParams;
  /** Downscaled Sobel map of the primary image, when edge snapping may run. */
  edge: EdgeMap | null;
};

export type MaskRegionOutcome = {
  version: number;
  count: number;
  bbox: MaskRect | null;
  /** Additions as a drawable layer, or null when nothing was added. */
  source: CanvasImageSource | null;
  /** True when a newer request replaced this one before it could run. */
  superseded: boolean;
};

/** Composer output in worker-friendly form (no canvas involved). */
export type MaskRegionResult = {
  additions: Uint8Array;
  count: number;
  bbox: MaskRect | null;
};

/** Message sent to the worker for an edge-map build. */
export type MaskEdgeRequest = {
  kind: 'edge';
  id: number;
  bitmap: ImageBitmap;
  width: number;
  height: number;
  scale: number;
  sourceWidth: number;
  sourceHeight: number;
};

export type MaskRegionWorkerRequest = ({ kind: 'region' } & MaskRegionRequest) | MaskEdgeRequest;

export type MaskRegionWorkerResponse =
  | {
      kind: 'region';
      version: number;
      count: number;
      bbox: MaskRect | null;
      bitmap?: ImageBitmap;
      additions?: Uint8Array;
    }
  | { kind: 'edge'; id: number; ok: true; edge: EdgeMap }
  | { kind: 'edge'; id: number; ok: false };

export const REGION_STRENGTH_DEFAULT: MaskRegionStrength = 'normal';
export const REGION_GROW_MAX = 32;

/** Light / normal / strong scale the closing and hole radii. */
const STRENGTH_SCALE: Record<MaskRegionStrength, number> = {
  light: 0.5,
  normal: 1,
  strong: 2
};

/** Chamfer 3-4 stores distances in thirds of a pixel. */
const CHAMFER_UNIT = 3;
const CHAMFER_MAX = 65535;

/** Edge-snap reach clamp, in image pixels (the size-derived default caps at 64). */
export const REGION_EDGE_RANGE_MIN = 2;
export const REGION_EDGE_RANGE_MAX = 64;
const EDGE_RANGE_MIN = REGION_EDGE_RANGE_MIN;
const EDGE_RANGE_MAX = REGION_EDGE_RANGE_MAX;

/** Marks this far from the frame are not pulled to it, whatever the reach. */
const BORDER_SNAP_MAX = 128;

/**
 * Resolve the user's choices into concrete pixel distances. Defaults are
 * proportional to the image so the same picture yields the same edit at any
 * zoom level, and small images keep sane floors.
 */
export function regionParamsFor(
  width: number,
  height: number,
  preferences: MaskRegionPreferences
): MaskRegionParams {
  const shortSide = Math.max(1, Math.min(width, height));
  const longSide = Math.max(1, Math.max(width, height));
  const scale = STRENGTH_SCALE[preferences.strength] ?? 1;
  const edgeDefault = Math.min(
    EDGE_RANGE_MAX,
    Math.max(8, Math.round(longSide * 0.02))
  );
  return {
    close: Math.max(1, Math.round(shortSide * 0.003 * scale)),
    borderSnap: Math.min(BORDER_SNAP_MAX, Math.max(2, Math.round(shortSide * 0.01 * scale))),
    holeRadius: Math.max(2, Math.round(shortSide * 0.015 * scale)),
    grow: Math.min(REGION_GROW_MAX, Math.max(0, Math.round(preferences.grow))),
    edgeSnap: preferences.edgeSnap,
    edgeRange: Math.min(
      EDGE_RANGE_MAX,
      Math.max(EDGE_RANGE_MIN, Math.round(preferences.edgeRange ?? edgeDefault))
    )
  };
}

/**
 * Chamfer 3-4 distance to the nearest set pixel, in third-of-a-pixel units.
 *
 * Two raster passes (forward, then backward) over a `Uint16Array`; a true
 * Euclidean transform is an order of magnitude slower and its ~8% octagonal
 * error is irrelevant to mask contours.
 */
export function chamfer34(binary: Uint8Array, width: number, height: number): Uint16Array {
  const count = width * height;
  const distance = new Uint16Array(count).fill(CHAMFER_MAX);
  for (let index = 0; index < count; index += 1) {
    if (binary[index]) distance[index] = 0;
  }

  for (let y = 0; y < height; y += 1) {
    const row = y * width;
    for (let x = 0; x < width; x += 1) {
      const index = row + x;
      let best = distance[index];
      if (!best) continue;
      if (x > 0 && distance[index - 1] + CHAMFER_UNIT < best) best = distance[index - 1] + CHAMFER_UNIT;
      if (y > 0) {
        if (distance[index - width] + CHAMFER_UNIT < best) best = distance[index - width] + CHAMFER_UNIT;
        if (x > 0 && distance[index - width - 1] + 4 < best) best = distance[index - width - 1] + 4;
        if (x < width - 1 && distance[index - width + 1] + 4 < best) {
          best = distance[index - width + 1] + 4;
        }
      }
      distance[index] = best;
    }
  }

  for (let y = height - 1; y >= 0; y -= 1) {
    const row = y * width;
    for (let x = width - 1; x >= 0; x -= 1) {
      const index = row + x;
      let best = distance[index];
      if (!best) continue;
      if (x < width - 1 && distance[index + 1] + CHAMFER_UNIT < best) {
        best = distance[index + 1] + CHAMFER_UNIT;
      }
      if (y < height - 1) {
        if (distance[index + width] + CHAMFER_UNIT < best) best = distance[index + width] + CHAMFER_UNIT;
        if (x < width - 1 && distance[index + width + 1] + 4 < best) {
          best = distance[index + width + 1] + 4;
        }
        if (x > 0 && distance[index + width - 1] + 4 < best) {
          best = distance[index + width - 1] + 4;
        }
      }
      distance[index] = best;
    }
  }

  return distance;
}

/** Every pixel within `radius` image pixels of the set. */
export function growWithin(
  binary: Uint8Array,
  distance: Uint16Array,
  radius: number
): Uint8Array {
  const grown = new Uint8Array(binary.length);
  if (radius <= 0) {
    grown.set(binary);
    return grown;
  }
  const limit = radius * CHAMFER_UNIT;
  for (let index = 0; index < grown.length; index += 1) {
    grown[index] = binary[index] || distance[index] <= limit ? 1 : 0;
  }
  return grown;
}

/**
 * Close gaps narrower than `2 * radius`: dilate by `radius`, then erode by the
 * same distance. Morphological closing is extensive, so the result always
 * contains the input (I1) while joining the thin seams between brush strokes.
 */
export function closeGaps(
  binary: Uint8Array,
  width: number,
  height: number,
  radius: number
): Uint8Array {
  if (radius <= 0) return binary.slice();

  const dilated = growWithin(binary, chamfer34(binary, width, height), radius);
  const complement = new Uint8Array(dilated.length);
  for (let index = 0; index < dilated.length; index += 1) {
    complement[index] = dilated[index] ? 0 : 1;
  }

  // Eroding by `radius` keeps the pixels whose whole radius-neighbourhood is
  // inside the dilated set, i.e. everything farther than `radius` from the
  // complement of that set.
  const distance = chamfer34(complement, width, height);
  const limit = radius * CHAMFER_UNIT;
  const closed = new Uint8Array(dilated.length);
  for (let index = 0; index < closed.length; index += 1) {
    closed[index] = distance[index] > limit ? 1 : 0;
  }
  return closed;
}

/**
 * Bridge the run between the marked region and the picture frame. For every
 * row and column, when a marked pixel sits within `reach` of an edge, the
 * unmarked pixels between that pixel and the frame are filled in — so a stroke
 * that stops a few pixels short no longer leaves a hairline of untouched
 * picture along the border.
 */
export function snapToBorder(
  binary: Uint8Array,
  width: number,
  height: number,
  reach: number
): Uint8Array {
  const snapped = binary.slice();
  if (reach <= 0) return snapped;
  const span = Math.max(1, Math.min(Math.round(reach), width, height));

  // `offset` is the distance from the edge, so the marked pixel that triggers
  // a fill sits *at* `reach` (offset ≤ span), not one short of it.
  for (let y = 0; y < height; y += 1) {
    const row = y * width;
    for (let offset = 0; offset <= span && offset < width; offset += 1) {
      if (binary[row + offset]) {
        for (let fill = 0; fill < offset; fill += 1) snapped[row + fill] = 1;
        break;
      }
    }
    for (let offset = 0; offset <= span && offset < width; offset += 1) {
      const x = width - 1 - offset;
      if (binary[row + x]) {
        for (let fill = x + 1; fill < width; fill += 1) snapped[row + fill] = 1;
        break;
      }
    }
  }

  for (let x = 0; x < width; x += 1) {
    for (let offset = 0; offset <= span && offset < height; offset += 1) {
      if (binary[offset * width + x]) {
        for (let fill = 0; fill < offset; fill += 1) snapped[fill * width + x] = 1;
        break;
      }
    }
    for (let offset = 0; offset <= span && offset < height; offset += 1) {
      const y = height - 1 - offset;
      if (binary[y * width + x]) {
        for (let fill = y + 1; fill < height; fill += 1) snapped[fill * width + x] = 1;
        break;
      }
    }
  }

  return snapped;
}

/**
 * Fill enclosed holes up to `radius` (measured by inradius, not area, so a long
 * thin unpainted seam is filled while a deliberate keep-out region is not).
 *
 * A hole is only filled when it does not touch the frame, its deepest point is
 * within `radius * 3` chamfer units of the marked set, and it contains no
 * protected pixel at all (I2): erasing one pixel inside a hole keeps the whole
 * hole.
 */
export function fillHoles(
  binary: Uint8Array,
  width: number,
  height: number,
  radius: number,
  protect: Uint8Array | null,
  distance: Uint16Array
): Uint8Array {
  const filled = binary.slice();
  if (radius <= 0 || !width || !height) return filled;
  const limit = radius * CHAMFER_UNIT;

  // Everything reachable from the frame without crossing a marked pixel is
  // outside the selection; whatever unmarked pixels remain are enclosed.
  const outside = new Uint8Array(binary.length);
  const stack = new Int32Array(binary.length);
  let top = 0;
  const seed = (index: number) => {
    if (!binary[index] && !outside[index]) {
      outside[index] = 1;
      stack[top++] = index;
    }
  };
  for (let x = 0; x < width; x += 1) {
    seed(x);
    seed((height - 1) * width + x);
  }
  for (let y = 0; y < height; y += 1) {
    seed(y * width);
    seed(y * width + width - 1);
  }
  while (top) {
    const index = stack[--top];
    const x = index - Math.floor(index / width) * width;
    if (x > 0) seed(index - 1);
    if (x < width - 1) seed(index + 1);
    if (index >= width) seed(index - width);
    if (index < binary.length - width) seed(index + width);
  }

  const visited = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    if (binary[index] || outside[index] || visited[index]) continue;

    const component = [index];
    visited[index] = 1;
    let deepest = 0;
    let touched = false;
    for (let cursor = 0; cursor < component.length; cursor += 1) {
      const current = component[cursor];
      if (distance[current] > deepest) deepest = distance[current];
      if (protect && protect[current]) touched = true;
      const x = current - Math.floor(current / width) * width;
      const push = (next: number) => {
        if (binary[next] || outside[next] || visited[next]) return;
        visited[next] = 1;
        component.push(next);
      };
      if (x > 0) push(current - 1);
      if (x < width - 1) push(current + 1);
      if (current >= width) push(current - width);
      if (current < binary.length - width) push(current + width);
    }

    if (touched || deepest > limit) continue;
    for (const pixel of component) filled[pixel] = 1;
  }

  return filled;
}

/**
 * Band watershed with a distance prior (plan §2.4): the selection is grown
 * into the undecided band around it, preferring strong edges, and the growth
 * stops near `radius` where no edge is found.
 *
 * Seeds are min-pooled blocks of `binary` (only blocks that are entirely marked
 * count as foreground) and blocks that are either farther than `radius` from
 * the foreground or contain a protected pixel. The rest is the band, resolved
 * by flooding in priority order, each pixel labelled the moment it is enqueued.
 *
 * Returns a source-resolution layer of the pixels the foreground won.
 */
export function bandWatershed(
  binary: Uint8Array,
  protect: Uint8Array | null,
  width: number,
  height: number,
  edge: EdgeMap,
  radius: number
): Uint8Array {
  const won = new Uint8Array(binary.length);
  const mapWidth = Math.max(1, Math.floor(edge.width));
  const mapHeight = Math.max(1, Math.floor(edge.height));
  if (!width || !height || mapWidth * mapHeight < 4) return won;

  const scaleX = mapWidth / width;
  const scaleY = mapHeight / height;
  const bandPx = Math.max(1, Math.round(radius * Math.min(scaleX, scaleY)));

  // Min-pool the mark and protection mirrors down to the map grid: a block is
  // foreground only when every source pixel under it is marked.
  const boundariesX = new Int32Array(mapWidth + 1);
  const boundariesY = new Int32Array(mapHeight + 1);
  for (let index = 0; index <= mapWidth; index += 1) {
    boundariesX[index] = Math.min(width, Math.floor((index * width) / mapWidth));
  }
  for (let index = 0; index <= mapHeight; index += 1) {
    boundariesY[index] = Math.min(height, Math.floor((index * height) / mapHeight));
  }
  boundariesX[mapWidth] = width;
  boundariesY[mapHeight] = height;

  const mapCount = mapWidth * mapHeight;
  const foreground = new Uint8Array(mapCount);
  const protectedBlock = new Uint8Array(mapCount);
  for (let mapY = 0; mapY < mapHeight; mapY += 1) {
    const yStart = boundariesY[mapY];
    const yEnd = Math.max(yStart + 1, boundariesY[mapY + 1]);
    for (let mapX = 0; mapX < mapWidth; mapX += 1) {
      const xStart = boundariesX[mapX];
      const xEnd = Math.max(xStart + 1, boundariesX[mapX + 1]);
      const block = mapY * mapWidth + mapX;
      let fullyMarked = true;
      let hasProtected = false;
      let scanningProtection = protect !== null;
      for (let y = yStart; y < yEnd; y += 1) {
        if (!fullyMarked && !scanningProtection) break;
        const row = y * width;
        for (let x = xStart; x < xEnd; x += 1) {
          const index = row + x;
          if (fullyMarked && !binary[index]) fullyMarked = false;
          if (scanningProtection && protect && protect[index]) {
            hasProtected = true;
            scanningProtection = false;
          }
        }
      }
      foreground[block] = fullyMarked ? 1 : 0;
      protectedBlock[block] = hasProtected ? 1 : 0;
    }
  }

  const distance = chamfer34(foreground, mapWidth, mapHeight);
  const units = bandPx * CHAMFER_UNIT;
  const label = new Uint8Array(mapCount); // 1 = region, 2 = keep, 0 = undecided
  for (let block = 0; block < mapCount; block += 1) {
    if (foreground[block]) label[block] = 1;
    else if (protectedBlock[block] || distance[block] > units) label[block] = 2;
  }

  const magnitude = edge.magnitude;
  const hasMagnitude = magnitude.length >= mapCount;
  const priorFor = (block: number) => {
    const beyond = Math.max(0, units - distance[block]);
    return Math.round((SNAP_MIN_STRENGTH * beyond) / units);
  };
  const levelCount = 256 + SNAP_MIN_STRENGTH + 1;
  const buckets: number[][] = Array.from({ length: levelCount }, () => []);
  // A pixel takes the label of whichever neighbour is reached first, so the
  // priority is decided at enqueue time and never revisited. The level is
  // clamped to the sweep's current one: a pixel can otherwise be enqueued into
  // a bucket that has already drained, leaving it labelled but never spread,
  // which would cut a hole in the territory of the label that owns it.
  const enqueue = (block: number, value: number, level: number) => {
    if (label[block]) return;
    label[block] = value;
    const strength = hasMagnitude ? magnitude[block] : 0;
    buckets[Math.min(levelCount - 1, Math.max(level, strength + priorFor(block)))].push(block);
  };
  const spread = (block: number, value: number, level: number) => {
    const x = block - Math.floor(block / mapWidth) * mapWidth;
    if (x > 0) enqueue(block - 1, value, level);
    if (x < mapWidth - 1) enqueue(block + 1, value, level);
    if (block >= mapWidth) enqueue(block - mapWidth, value, level);
    if (block < mapCount - mapWidth) enqueue(block + mapWidth, value, level);
  };

  // Snapshot the seeds before spreading any of them: interleaving the two
  // would let a seed label a band pixel and spread it again in the same pass,
  // and since the sweep is in raster order the whole band would cascade to
  // whichever label happened to sit earlier in the buffer.
  const seeds: number[] = [];
  for (let block = 0; block < mapCount; block += 1) {
    if (label[block]) seeds.push(block);
  }
  for (const block of seeds) spread(block, label[block], 0);

  for (let level = 0; level < levelCount; level += 1) {
    const bucket = buckets[level];
    while (bucket.length) {
      const block = bucket.pop() as number;
      spread(block, label[block], level);
    }
  }

  for (let y = 0; y < height; y += 1) {
    const mapY = Math.min(mapHeight - 1, Math.floor((y * mapHeight) / height));
    const row = y * width;
    for (let x = 0; x < width; x += 1) {
      const mapX = Math.min(mapWidth - 1, Math.floor((x * mapWidth) / width));
      if (label[mapY * mapWidth + mapX] === 1) won[row + x] = 1;
    }
  }
  return won;
}

/** Copy one rect out of a full-frame bitmap into a tight row-major buffer. */
export function cropRegion(source: Uint8Array, width: number, bbox: MaskRect): Uint8Array {
  const cropped = new Uint8Array(bbox.width * bbox.height);
  for (let row = 0; row < bbox.height; row += 1) {
    const start = (bbox.y + row) * width + bbox.x;
    cropped.set(source.subarray(start, start + bbox.width), row * bbox.width);
  }
  return cropped;
}

/**
 * Build a drawable layer for a binary additions buffer, in the same emerald as
 * the marks so the editor can tint it. Returns null when neither an
 * OffscreenCanvas (workers, Safari 16.4+) nor a document is available.
 */
export function additionsLayer(
  additions: Uint8Array,
  bbox: MaskRect
): { source: CanvasImageSource; bbox: MaskRect } | null {
  if (bbox.width <= 0 || bbox.height <= 0) return null;

  const createSurface = (): HTMLCanvasElement | OffscreenCanvas | null => {
    if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(bbox.width, bbox.height);
    if (typeof document === 'undefined') return null;
    const canvas = document.createElement('canvas');
    canvas.width = bbox.width;
    canvas.height = bbox.height;
    return canvas;
  };
  const surface = createSurface();
  if (!surface) return null;
  const context = surface.getContext('2d', { willReadFrequently: true }) as
    | CanvasRenderingContext2D
    | OffscreenCanvasRenderingContext2D
    | null;
  if (!context) return null;

  const imageData = context.createImageData(bbox.width, bbox.height);
  const data = imageData.data;
  for (let index = 0, pixel = 0; index < additions.length; index += 1, pixel += 4) {
    if (!additions[index]) continue;
    data[pixel] = 16;
    data[pixel + 1] = 185;
    data[pixel + 2] = 129;
    data[pixel + 3] = 255;
  }
  context.putImageData(imageData, 0, 0);
  return {
    source:
      typeof OffscreenCanvas === 'function' && surface instanceof OffscreenCanvas
        ? surface.transferToImageBitmap()
        : (surface as HTMLCanvasElement),
    bbox
  };
}

/** Merge `addition` into `binary` in place and return it. */
function mergeInto(binary: Uint8Array, addition: Uint8Array): Uint8Array {
  for (let index = 0; index < binary.length; index += 1) {
    if (addition[index]) binary[index] = 1;
  }
  return binary;
}

/**
 * Run the whole gap-fill pipeline and return only what it added.
 *
 * The additions layer is `result \ a \ p`, so it never overlaps the marks
 * themselves (I1: the caller draws it on top of them) and never restores
 * something the user erased (I2).
 */
export function composeRegion(input: {
  width: number;
  height: number;
  a: Uint8Array;
  p: Uint8Array | null;
  params: MaskRegionParams;
  edge?: EdgeMap | null;
}, allowCrop = true): MaskRegionResult {
  const { width, height, a, p, params } = input;
  const count = width * height;
  const additions = new Uint8Array(count);
  const empty: MaskRegionResult = { additions, count: 0, bbox: null };
  if (count <= 0 || !a.length) return empty;

  // Most brush and shape selections occupy only part of a large image. The
  // morphology needs the marks and a reach-sized margin, while an unrelated
  // blank frame costs two full distance transforms plus a flood scan. Crop
  // only when every side stays beyond the original-frame snap reach; then
  // border snapping cannot contribute and the crop edges are genuinely open.
  if (allowCrop && !params.edgeSnap) {
    let minX = width;
    let minY = height;
    let maxX = -1;
    let maxY = -1;
    for (let index = 0; index < count; index += 1) {
      if (!a[index]) continue;
      const x = index % width;
      const y = (index - x) / width;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (maxX < 0) return empty;
    const reach = 2 * params.close + params.borderSnap + params.holeRadius + params.grow + 2;
    if (minX > reach && minY > reach && width - 1 - maxX > reach && height - 1 - maxY > reach) {
      const rect = {
        x: minX - reach,
        y: minY - reach,
        width: maxX - minX + 1 + 2 * reach,
        height: maxY - minY + 1 + 2 * reach
      };
      if (rect.width * rect.height < count * 0.8) {
        const cropped = composeRegion({
          width: rect.width,
          height: rect.height,
          a: cropRegion(a, width, rect),
          p: p ? cropRegion(p, width, rect) : null,
          params: { ...params, borderSnap: 0 }
        }, false);
        if (!cropped.bbox) return empty;
        for (let y = 0; y < rect.height; y += 1) {
          additions.set(
            cropped.additions.subarray(y * rect.width, (y + 1) * rect.width),
            (rect.y + y) * width + rect.x
          );
        }
        return {
          additions,
          count: cropped.count,
          bbox: { ...cropped.bbox, x: cropped.bbox.x + rect.x, y: cropped.bbox.y + rect.y }
        };
      }
    }
  }

  // ① close gaps, ② pull to the frame, ③ fill enclosed holes. All three are
  // idempotent, which is what lets a reopened mask keep them enabled.
  let region = params.close > 0 ? closeGaps(a, width, height, params.close) : a.slice();
  if (params.borderSnap > 0) region = snapToBorder(region, width, height, params.borderSnap);
  const distance = chamfer34(region, width, height);
  if (params.holeRadius > 0) {
    region = fillHoles(region, width, height, params.holeRadius, p, distance);
  }
  // ④ edge snap is heuristic (and not idempotent), so it stays opt-in.
  if (params.edgeSnap && input.edge) {
    region = mergeInto(
      region,
      bandWatershed(region, p, width, height, input.edge, params.edgeRange)
    );
  }
  // ⑤ dilation reuses the distance transform computed above.
  if (params.grow > 0) region = growWithin(region, distance, params.grow);

  let marked = 0;
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < height; y += 1) {
    const row = y * width;
    for (let x = 0; x < width; x += 1) {
      const index = row + x;
      if (!region[index] || a[index] || (p && p[index])) continue;
      additions[index] = 1;
      marked += 1;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }

  if (!marked) return empty;
  return {
    additions,
    count: marked,
    bbox: { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 }
  };
}
