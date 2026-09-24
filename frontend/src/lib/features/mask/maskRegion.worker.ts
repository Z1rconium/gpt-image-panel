/// <reference lib="webworker" />

/**
 * Region gap-fill worker.
 *
 * Both passes here are pure CPU work on plain buffers — the morphological
 * pipeline that resolves the holes, seams and border strips of a selection,
 * and the Sobel edge map the editor snaps to. Running them off the main thread
 * is the point: at 4096² the default chain costs roughly half a second, which
 * would otherwise land on the frame the pointer was released on.
 *
 * Only created from maskRegionClient.ts (same-origin module worker; see the
 * `worker-src 'self'` directive in backend/app/api/csp.py).
 */

import {
  additionsLayer,
  composeRegion,
  cropRegion,
  type MaskRegionWorkerRequest,
  type MaskRegionWorkerResponse
} from './maskRegion';
import { computeEdgeMap } from './maskSnap';

const scope = self as unknown as DedicatedWorkerGlobalScope;

function post(message: MaskRegionWorkerResponse, transfer: Transferable[] = []) {
  scope.postMessage(message, transfer);
}

function handleRegion(request: Extract<MaskRegionWorkerRequest, { kind: 'region' }>) {
  const { version, width, height, a, p, params, edge } = request;
  const result = composeRegion({ width, height, a, p, params, edge });
  if (!result.count || !result.bbox) {
    post({ kind: 'region', version, count: 0, bbox: null });
    return;
  }

  const layer = additionsLayer(cropRegion(result.additions, width, result.bbox), result.bbox);
  if (layer && typeof ImageBitmap !== 'undefined' && layer.source instanceof ImageBitmap) {
    post(
      { kind: 'region', version, count: result.count, bbox: result.bbox, bitmap: layer.source },
      [layer.source]
    );
    return;
  }

  // No OffscreenCanvas (pre-16.4 Safari): hand back the tight bitmap and let
  // the editor put it on a small canvas itself.
  const cropped = cropRegion(result.additions, width, result.bbox);
  post({ kind: 'region', version, count: result.count, bbox: result.bbox, additions: cropped }, [
    cropped.buffer
  ]);
}

function handleEdge(request: Extract<MaskRegionWorkerRequest, { kind: 'edge' }>) {
  const { id, bitmap, width, height, scale, sourceWidth, sourceHeight } = request;
  try {
    if (typeof OffscreenCanvas !== 'function') {
      bitmap.close();
      post({ kind: 'edge', id, ok: false });
      return;
    }
    const canvas = new OffscreenCanvas(width, height);
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) {
      bitmap.close();
      post({ kind: 'edge', id, ok: false });
      return;
    }
    context.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();
    const pixels = context.getImageData(0, 0, width, height);
    const edge = computeEdgeMap(pixels.data, width, height, { scale, sourceWidth, sourceHeight });
    post({ kind: 'edge', id, ok: true, edge }, [edge.magnitude.buffer]);
  } catch {
    post({ kind: 'edge', id, ok: false });
  }
}

scope.onmessage = (event: MessageEvent<MaskRegionWorkerRequest>) => {
  const request = event.data;
  if (!request) return;
  if (request.kind === 'edge') handleEdge(request);
  else handleRegion(request);
};
