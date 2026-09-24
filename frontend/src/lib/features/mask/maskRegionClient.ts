/**
 * Main-thread owner of the gap-fill worker.
 *
 * The editor produces snapshots far faster than the pipeline can consume them —
 * every pointer release, undo and slider move bumps the region version — so the
 * client keeps at most one request in flight and one queued, answering the ones
 * it replaces with `superseded`. Whoever asked then simply checks the version
 * that came back.
 *
 * When `Worker` is missing or fails to construct (or OffscreenCanvas is not
 * available for an edge map), the same pure functions run synchronously on the
 * main thread: results are identical, only the frame pacing is worse.
 */

import {
  additionsLayer,
  composeRegion,
  type MaskEdgeRequest,
  type MaskRegionOutcome,
  type MaskRegionRequest,
  type MaskRegionWorkerResponse
} from './maskRegion';
import type { EdgeMap } from './maskSnap';

export type MaskRegionClientOptions = {
  /** Injected in tests; defaults to a module worker built from maskRegion.worker.ts. */
  createWorker?: () => Worker;
};

function supersededOutcome(version: number): MaskRegionOutcome {
  return { version, count: 0, bbox: null, source: null, superseded: true };
}

export class MaskRegionClient {
  private readonly createWorker: () => Worker;
  private worker: Worker | null = null;
  private workerFailed = false;
  private nextEdgeId = 1;
  private edgeResolvers = new Map<number, (edge: EdgeMap | null) => void>();
  private regionResolvers = new Map<number, (outcome: MaskRegionOutcome) => void>();
  private queued: { request: MaskRegionRequest; resolve: (outcome: MaskRegionOutcome) => void } | null =
    null;
  private inflight: MaskRegionRequest | null = null;

  constructor(options: MaskRegionClientOptions = {}) {
    this.createWorker =
      options.createWorker ??
      (() => new Worker(new URL('./maskRegion.worker.ts', import.meta.url), { type: 'module' }));
  }

  /** Run one gap-fill pass; the newest request always wins. */
  requestRegion(request: MaskRegionRequest): Promise<MaskRegionOutcome> {
    if (!this.ensureWorker()) return Promise.resolve(this.computeLocally(request));

    return new Promise<MaskRegionOutcome>((resolve) => {
      const replaced = this.queued;
      this.queued = { request, resolve };
      if (replaced) replaced.resolve(supersededOutcome(replaced.request.version));
      if (!this.inflight) this.dispatchQueued();
    });
  }

  /**
   * Build the Sobel edge map off the main thread. The bitmap is consumed here:
   * a null result means "build it the old way instead", which the editor does
   * from the image element rather than from this bitmap.
   */
  requestEdgeMap(
    bitmap: ImageBitmap,
    size: { width: number; height: number; scale: number; sourceWidth: number; sourceHeight: number }
  ): Promise<EdgeMap | null> {
    const worker = this.ensureWorker();
    if (!worker) {
      bitmap.close();
      return Promise.resolve(null);
    }
    const id = this.nextEdgeId;
    this.nextEdgeId += 1;
    return new Promise<EdgeMap | null>((resolve) => {
      this.edgeResolvers.set(id, resolve);
      const request: MaskEdgeRequest = { kind: 'edge', id, bitmap, ...size };
      worker.postMessage(request, [bitmap]);
    });
  }

  /** True once the worker has proven unusable; callers then use the local path. */
  get unavailable(): boolean {
    return this.workerFailed;
  }

  teardown() {
    this.worker?.terminate();
    this.worker = null;
    // Answer everything still waiting: a caller blocked on a result that will
    // never arrive would otherwise never run its own cleanup.
    for (const [version, resolve] of this.regionResolvers) resolve(supersededOutcome(version));
    for (const resolve of this.edgeResolvers.values()) resolve(null);
    this.regionResolvers.clear();
    this.edgeResolvers.clear();
    this.queued = null;
    this.inflight = null;
  }

  private ensureWorker(): Worker | null {
    if (this.worker) return this.worker;
    if (this.workerFailed) return null;
    if (typeof Worker === 'undefined') {
      this.workerFailed = true;
      return null;
    }
    try {
      const worker = this.createWorker();
      worker.onmessage = (event: MessageEvent<MaskRegionWorkerResponse>) =>
        this.handleMessage(event.data);
      worker.onerror = () => this.failWorker();
      worker.onmessageerror = () => this.failWorker();
      this.worker = worker;
      return worker;
    } catch {
      this.workerFailed = true;
      return null;
    }
  }

  private failWorker() {
    this.workerFailed = true;
    this.worker?.terminate();
    this.worker = null;
    for (const [version, resolve] of this.regionResolvers) resolve(supersededOutcome(version));
    this.regionResolvers.clear();
    for (const resolve of this.edgeResolvers.values()) resolve(null);
    this.edgeResolvers.clear();
    this.inflight = null;
    this.queued = null;
  }

  private handleMessage(message: MaskRegionWorkerResponse) {
    if (message.kind === 'edge') {
      const resolve = this.edgeResolvers.get(message.id);
      this.edgeResolvers.delete(message.id);
      resolve?.(message.ok ? message.edge : null);
      return;
    }

    const resolve = this.regionResolvers.get(message.version);
    this.regionResolvers.delete(message.version);
    this.inflight = null;

    // A worker with OffscreenCanvas sends a bitmap; one without sends the tight
    // additions buffer instead, which becomes a small canvas here.
    let source: CanvasImageSource | null = message.bitmap ?? null;
    if (!source && message.additions && message.bbox) {
      source = additionsLayer(message.additions, message.bbox)?.source ?? null;
    }
    resolve?.({
      version: message.version,
      count: message.count,
      bbox: message.bbox,
      source,
      superseded: false
    });
    this.dispatchQueued();
  }

  private dispatchQueued() {
    const next = this.queued;
    const worker = this.worker;
    if (!next || !worker) {
      this.queued = null;
      return;
    }
    this.queued = null;
    this.inflight = next.request;
    this.regionResolvers.set(next.request.version, next.resolve);
    const request: MaskRegionRequest & { kind: 'region' } = { kind: 'region', ...next.request };
    const transfer: Transferable[] = [next.request.a.buffer as ArrayBuffer];
    if (next.request.p) transfer.push(next.request.p.buffer as ArrayBuffer);
    worker.postMessage(request, transfer);
  }

  private computeLocally(request: MaskRegionRequest): MaskRegionOutcome {
    const result = composeRegion({
      width: request.width,
      height: request.height,
      a: request.a,
      p: request.p,
      params: request.params,
      edge: request.edge
    });
    const layer = result.bbox ? additionsLayer(result.additions, result.bbox) : null;
    return {
      version: request.version,
      count: result.count,
      bbox: result.bbox,
      source: layer?.source ?? null,
      superseded: false
    };
  }
}
