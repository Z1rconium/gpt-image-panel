// Pure viewport math for the markup editor. Everything here is expressed in two
// spaces: "image" pixels (the upright, EXIF-normalised primary image that the
// exported PNG matches 1:1) and "stage" CSS pixels (the on-screen canvas box).
// Device pixel ratio only changes backing-store sharpness, never these values.

export type MarkupPoint = { x: number; y: number };
export type MarkupView = { scale: number; x: number; y: number };
export type MarkupSize = { width: number; height: number };

export const MAX_VIEW_SCALE = 16;
// Edge slack so the user can still drag the image a little past the stage edge.
export const PAN_MARGIN_PX = 32;
export const FIT_PADDING_PX = 12;
const ABSOLUTE_MIN_SCALE = 0.005;

export function fitScale(stage: MarkupSize, image: MarkupSize, padding = FIT_PADDING_PX): number {
  if (stage.width <= 0 || stage.height <= 0 || image.width <= 0 || image.height <= 0) return 1;
  const availableWidth = Math.max(1, stage.width - padding * 2);
  const availableHeight = Math.max(1, stage.height - padding * 2);
  return Math.min(availableWidth / image.width, availableHeight / image.height);
}

/**
 * The smallest zoom allowed. It is derived from the fit scale rather than a
 * fixed constant, otherwise an extremely wide or tall image could never be
 * shown in full.
 */
export function minViewScale(fit: number): number {
  return Math.max(ABSOLUTE_MIN_SCALE, Math.min(fit, 1) * 0.5);
}

export function clampScale(scale: number, fit: number): number {
  const min = minViewScale(fit);
  const max = Math.max(MAX_VIEW_SCALE, fit);
  if (!Number.isFinite(scale)) return Math.min(Math.max(fit, min), max);
  return Math.min(max, Math.max(min, scale));
}

function clampAxis(offset: number, scaledSize: number, stageSize: number): number {
  if (scaledSize <= stageSize) return (stageSize - scaledSize) / 2;
  const min = stageSize - scaledSize - PAN_MARGIN_PX;
  const max = PAN_MARGIN_PX;
  return Math.min(max, Math.max(min, offset));
}

export function clampView(view: MarkupView, stage: MarkupSize, image: MarkupSize): MarkupView {
  return {
    scale: view.scale,
    x: clampAxis(view.x, image.width * view.scale, stage.width),
    y: clampAxis(view.y, image.height * view.scale, stage.height)
  };
}

export function fitView(stage: MarkupSize, image: MarkupSize): MarkupView {
  const scale = fitScale(stage, image);
  return clampView(
    { scale, x: (stage.width - image.width * scale) / 2, y: (stage.height - image.height * scale) / 2 },
    stage,
    image
  );
}

/** Zoom to `scale`, keeping the image point under `anchor` (stage px) fixed. */
export function zoomViewAt(
  view: MarkupView,
  scale: number,
  anchor: MarkupPoint,
  stage: MarkupSize,
  image: MarkupSize
): MarkupView {
  const next = clampScale(scale, fitScale(stage, image));
  const imageX = (anchor.x - view.x) / view.scale;
  const imageY = (anchor.y - view.y) / view.scale;
  return clampView({ scale: next, x: anchor.x - imageX * next, y: anchor.y - imageY * next }, stage, image);
}

export function zoomViewByFactor(
  view: MarkupView,
  factor: number,
  anchor: MarkupPoint,
  stage: MarkupSize,
  image: MarkupSize
): MarkupView {
  return zoomViewAt(view, view.scale * factor, anchor, stage, image);
}

export function panView(view: MarkupView, dx: number, dy: number, stage: MarkupSize, image: MarkupSize): MarkupView {
  return clampView({ ...view, x: view.x + dx, y: view.y + dy }, stage, image);
}

/**
 * Two-finger gesture: scale by the change in finger distance and translate by
 * the movement of the midpoint, anchored at the midpoint where the gesture began.
 */
export function pinchView(
  start: MarkupView,
  startCenter: MarkupPoint,
  startDistance: number,
  center: MarkupPoint,
  distance: number,
  stage: MarkupSize,
  image: MarkupSize
): MarkupView {
  if (startDistance <= 0 || distance <= 0) return start;
  const scale = clampScale(start.scale * (distance / startDistance), fitScale(stage, image));
  const imageX = (startCenter.x - start.x) / start.scale;
  const imageY = (startCenter.y - start.y) / start.scale;
  return clampView({ scale, x: center.x - imageX * scale, y: center.y - imageY * scale }, stage, image);
}

export function stageToImage(view: MarkupView, point: MarkupPoint): MarkupPoint {
  return { x: (point.x - view.x) / view.scale, y: (point.y - view.y) / view.scale };
}

export function imageToStage(view: MarkupView, point: MarkupPoint): MarkupPoint {
  return { x: point.x * view.scale + view.x, y: point.y * view.scale + view.y };
}

export function isInsideImage(point: MarkupPoint, image: MarkupSize): boolean {
  return point.x >= 0 && point.y >= 0 && point.x <= image.width && point.y <= image.height;
}

export function clampToImage(point: MarkupPoint, image: MarkupSize): MarkupPoint {
  return {
    x: Math.min(image.width, Math.max(0, point.x)),
    y: Math.min(image.height, Math.max(0, point.y))
  };
}

/**
 * Backing-store size for the viewport canvas. It follows the stage and the
 * device pixel ratio only; it never grows with the image or the zoom level.
 */
export function viewportBackingSize(stage: MarkupSize, devicePixelRatio: number, maxEdge = 4096): MarkupSize {
  const ratio = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  const width = Math.max(1, Math.round(stage.width * ratio));
  const height = Math.max(1, Math.round(stage.height * ratio));
  const shrink = Math.min(1, maxEdge / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * shrink)), height: Math.max(1, Math.round(height * shrink)) };
}
