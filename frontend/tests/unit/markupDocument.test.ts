import { describe, expect, it } from 'vitest';
import {
  MAX_HISTORY,
  MAX_TEXT_LENGTH,
  MarkupDocument,
  MarkupDocumentError,
  markupSizing,
  sanitizeMarkupCommand,
  validateMarkupSize,
  type MarkupCanvasFactory,
  type MarkupCanvasLike,
  type MarkupContext
} from '$lib/features/markup/markupDocument';

// A recording stand-in for a 2D canvas. Pixel-level behaviour is covered by
// the Playwright suite against a real browser canvas; these tests pin the
// history, validation and lifecycle logic that does not depend on pixels.
class FakeContext {
  log: string[] = [];
  props: Record<string, unknown> = {};
  alphaFill = 0;
  constructor(readonly canvas: FakeCanvas) {
    return new Proxy(this, {
      get: (target, key: string) => {
        if (key in target) return (target as unknown as Record<string, unknown>)[key];
        if (key in target.props) return target.props[key];
        return (...args: unknown[]) => {
          target.log.push(key);
          if (key === 'getImageData') {
            const [, , w, h] = args as number[];
            const data = new Uint8ClampedArray(w * h * 4);
            for (let i = 3; i < data.length; i += 4) data[i] = target.canvas.alpha;
            return { data };
          }
          return undefined;
        };
      },
      set: (target, key: string, value) => {
        target.props[key] = value;
        return true;
      }
    });
  }
}

class FakeCanvas implements MarkupCanvasLike {
  width: number;
  height: number;
  alpha = 0;
  ctx: FakeContext | null = null;
  constructor(width: number, height: number) {
    this.width = width;
    this.height = height;
  }
  getContext() {
    this.ctx ??= new FakeContext(this);
    return this.ctx as unknown as MarkupContext;
  }
  toBlob(callback: (blob: Blob | null) => void) {
    callback(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }));
  }
}

function harness() {
  const canvases: FakeCanvas[] = [];
  const createCanvas: MarkupCanvasFactory = (width, height) => {
    const canvas = new FakeCanvas(width, height);
    canvases.push(canvas);
    return canvas;
  };
  return { canvases, createCanvas };
}

const rect = (x1: number, y1: number, x2: number, y2: number) =>
  ({ kind: 'rect', color: '#e5322d', width: 4, x1, y1, x2, y2 }) as const;

describe('markup command validation', () => {
  const size = { width: 100, height: 80 };

  it('rejects zero-size shapes and empty text without touching the history', () => {
    expect(sanitizeMarkupCommand(rect(10, 10, 10, 10), size)).toBeNull();
    expect(sanitizeMarkupCommand({ kind: 'text', color: '#111111', size: 20, x: 1, y: 1, text: '   ' }, size)).toBeNull();
    expect(sanitizeMarkupCommand({ kind: 'arrow', color: 'red', width: 4, x1: 0, y1: 0, x2: 50, y2: 50 }, size)).toBeNull();
    expect(sanitizeMarkupCommand({ kind: 'pen', color: '#111111', width: Number.NaN, points: [1, 1] }, size)).toBeNull();
    expect(sanitizeMarkupCommand({ kind: 'pen', color: '#111111', width: 4, points: [1, 1, 2] }, size)).toBeNull();
  });

  it('clamps geometry into the image and limits sizes', () => {
    const clean = sanitizeMarkupCommand({ ...rect(-50, -50, 500, 500), width: 9999 }, size);
    expect(clean).toMatchObject({ x1: 0, y1: 0, x2: 100, y2: 80, width: 512 });
    const pen = sanitizeMarkupCommand({ kind: 'pen', color: '#2563EB', width: 0.2, points: [-5, 200, 5, 5] }, size);
    expect(pen).toMatchObject({ color: '#2563eb', width: 1, points: [0, 80, 5, 5] });
  });

  it('normalises text to a single trimmed line within the length limit', () => {
    const clean = sanitizeMarkupCommand(
      { kind: 'text', color: '#ffffff', size: 24, x: 5, y: 5, text: `  here\nthis ${'x'.repeat(200)}` },
      size
    );
    expect(clean && clean.kind === 'text' && clean.text.length).toBe(MAX_TEXT_LENGTH);
    expect(clean && clean.kind === 'text' && clean.text.includes('\n')).toBe(false);
  });

  it('enforces image size limits including a deployment pixel limit', () => {
    expect(() => validateMarkupSize(0, 10)).toThrow(MarkupDocumentError);
    expect(() => validateMarkupSize(4096, 4096)).not.toThrow();
    expect(() => validateMarkupSize(4097, 4096)).toThrow(MarkupDocumentError);
    expect(() => validateMarkupSize(2000, 2000, 1_000_000)).toThrowError(expect.objectContaining({ code: 'too_large' }));
  });

  it('scales default sizes with the image', () => {
    const small = markupSizing(512, 512);
    const large = markupSizing(4096, 4096);
    expect(large.strokeDefault).toBeGreaterThan(small.strokeDefault);
    expect(large.textDefault).toBeGreaterThan(small.textDefault);
    expect(small.strokeMax).toBeGreaterThanOrEqual(small.strokeDefault);
  });
});

describe('markup history', () => {
  it('supports undo, redo and truncates redo on a new command', () => {
    const { createCanvas } = harness();
    const doc = MarkupDocument.create(200, 100, { createCanvas });
    const initialKey = doc.stateKey;
    expect(doc.canUndo).toBe(false);
    expect(doc.commit(rect(10, 10, 60, 60))).toBe(true);
    expect(doc.commit({ kind: 'ellipse', color: '#16a34a', width: 3, x1: 20, y1: 20, x2: 90, y2: 70 })).toBe(true);
    expect(doc.historyDepth).toBe(2);
    expect(doc.undo()).toBe(true);
    expect(doc.canRedo).toBe(true);
    expect(doc.commit({ kind: 'arrow', color: '#111111', width: 3, x1: 5, y1: 5, x2: 150, y2: 90 })).toBe(true);
    expect(doc.canRedo).toBe(false);
    expect(doc.historyDepth).toBe(2);
    doc.undo();
    doc.undo();
    expect(doc.stateKey).toBe(initialKey);
    expect(doc.undo()).toBe(false);
    doc.dispose();
  });

  it('does not record zero-size shapes and ignores clear on an empty layer', () => {
    const { createCanvas } = harness();
    const doc = MarkupDocument.create(100, 100, { createCanvas });
    expect(doc.commit(rect(10, 10, 10, 10))).toBe(false);
    expect(doc.clear()).toBe(false);
    expect(doc.historyDepth).toBe(0);
    doc.commit(rect(10, 10, 40, 40));
    expect(doc.clear()).toBe(true);
    expect(doc.undo()).toBe(true);
    expect(doc.historyDepth).toBe(1);
  });

  it('keeps the visible content when history exceeds the limit', () => {
    const { createCanvas, canvases } = harness();
    const doc = MarkupDocument.create(300, 300, { createCanvas });
    for (let i = 0; i < MAX_HISTORY + 15; i += 1) {
      expect(doc.commit(rect(i, i, i + 20, i + 20))).toBe(true);
    }
    expect(doc.historyDepth).toBe(MAX_HISTORY);
    // Work layer plus a lazily allocated base checkpoint.
    expect(canvases).toHaveLength(2);
    const base = canvases[1];
    expect(base.ctx?.log.filter((entry) => entry === 'strokeRect')).toHaveLength(15);
    for (let i = 0; i < MAX_HISTORY; i += 1) expect(doc.undo()).toBe(true);
    expect(doc.canUndo).toBe(false);
    // The folded marks are replayed from the checkpoint rather than dropped.
    const work = canvases[0];
    const before = work.ctx?.log.filter((entry) => entry === 'drawImage').length ?? 0;
    expect(before).toBeGreaterThan(0);
    doc.dispose();
  });

  it('does not allow undo or commit while a stroke is in progress', () => {
    const { createCanvas } = harness();
    const doc = MarkupDocument.create(100, 100, { createCanvas });
    doc.commit(rect(5, 5, 30, 30));
    expect(doc.beginStroke('pen', { x: 10, y: 10 }, { color: '#e5322d', width: 4 })).toBe(true);
    expect(doc.canUndo).toBe(false);
    expect(() => doc.commit(rect(1, 1, 20, 20))).toThrowError(expect.objectContaining({ code: 'busy' }));
    doc.extendStroke({ x: 40, y: 40 });
    expect(doc.endStroke()).toBe(true);
    expect(doc.historyDepth).toBe(2);
  });
});

describe('markup strokes', () => {
  it('records one history entry per stroke and a dot for a single tap', () => {
    const { createCanvas } = harness();
    const doc = MarkupDocument.create(100, 100, { createCanvas });
    doc.beginStroke('pen', { x: 10, y: 10 }, { color: '#e5322d', width: 6 });
    doc.extendStroke({ x: 20, y: 20 });
    doc.extendStroke({ x: 20, y: 20 });
    doc.extendStroke({ x: 30, y: 25 });
    doc.endStroke();
    expect(doc.historyDepth).toBe(1);
    doc.beginStroke('pen', { x: 50, y: 50 }, { color: '#e5322d', width: 6 });
    doc.endStroke();
    expect(doc.historyDepth).toBe(2);
    expect(doc.undo()).toBe(true);
    expect(doc.undo()).toBe(true);
    expect(doc.undo()).toBe(false);
  });

  it('cancelling an unfinished stroke leaves nothing in the history', () => {
    const { createCanvas } = harness();
    const doc = MarkupDocument.create(100, 100, { createCanvas });
    doc.beginStroke('erase', { x: 10, y: 10 }, { color: '#000000', width: 6 });
    doc.extendStroke({ x: 70, y: 70 });
    expect(doc.cancelStroke()).toBe(true);
    expect(doc.hasActiveStroke).toBe(false);
    expect(doc.historyDepth).toBe(0);
    expect(doc.endStroke()).toBe(false);
  });

  it('clamps stroke points to the image', async () => {
    const { createCanvas, canvases } = harness();
    const doc = MarkupDocument.create(100, 100, { createCanvas });
    doc.beginStroke('pen', { x: -20, y: 500 }, { color: '#e5322d', width: 4 });
    doc.extendStroke({ x: 400, y: -9 });
    doc.endStroke();
    canvases[0].alpha = 128;
    const { state } = await doc.exportApplied();
    expect(state.commands[0]).toMatchObject({ kind: 'pen', points: [0, 100, 100, 0] });
    doc.dispose();
  });
});

describe('markup export and editor state', () => {
  it('refuses to export an empty or fully opaque layer', async () => {
    const { createCanvas, canvases } = harness();
    const doc = MarkupDocument.create(64, 64, { createCanvas });
    doc.commit(rect(5, 5, 40, 40));
    canvases[0].alpha = 0;
    await expect(doc.exportApplied()).rejects.toMatchObject({ code: 'empty' });
    canvases[0].alpha = 255;
    await expect(doc.exportApplied()).rejects.toMatchObject({ code: 'opaque' });
    doc.dispose();
  });

  it('exports the native size with an immutable editor state', async () => {
    const { createCanvas, canvases } = harness();
    const doc = MarkupDocument.create(64, 48, { createCanvas });
    doc.commit(rect(5, 5, 40, 40));
    canvases[0].alpha = 128;
    const result = await doc.exportApplied();
    expect(result.width).toBe(64);
    expect(result.height).toBe(48);
    expect(result.blob.type).toBe('image/png');
    expect(result.state.base).toBeNull();
    expect(result.state.commands).toHaveLength(1);
    expect(Object.isFrozen(result.state)).toBe(true);
    expect(Object.isFrozen(result.state.commands)).toBe(true);
    expect(() => (result.state.commands as unknown as unknown[]).push({})).toThrow();
    doc.dispose();
  });

  it('keeps the working copy independent from an applied state', async () => {
    const { createCanvas, canvases } = harness();
    const first = MarkupDocument.create(64, 48, { createCanvas });
    first.commit(rect(5, 5, 40, 40));
    canvases[0].alpha = 128;
    const applied = (await first.exportApplied()).state;
    first.dispose();

    const copy = await MarkupDocument.fromState(applied, { createCanvas });
    expect(copy.historyDepth).toBe(1);
    copy.commit(rect(1, 1, 30, 30));
    copy.undo();
    copy.undo();
    expect(copy.historyDepth).toBe(0);
    // The applied state is untouched by anything the working copy did.
    expect(applied.commands).toHaveLength(1);
    copy.dispose();
  });

  it('rejects a stale export when the document changed while encoding', async () => {
    const { createCanvas, canvases } = harness();
    const doc = MarkupDocument.create(64, 64, { createCanvas });
    doc.commit(rect(5, 5, 40, 40));
    canvases[0].alpha = 128;
    const pending = doc.exportApplied();
    doc.commit(rect(10, 10, 50, 50));
    await expect(pending).rejects.toMatchObject({ code: 'stale' });
    doc.dispose();
  });

  it('imports a base PNG with the expected size only', async () => {
    const { createCanvas } = harness();
    const bitmap = (width: number, height: number) =>
      Promise.resolve({ width, height, close() {} } as unknown as ImageBitmap);
    const blob = new Blob([new Uint8Array([1])], { type: 'image/png' });
    const doc = await MarkupDocument.fromPng(blob, { width: 32, height: 16 }, { createCanvas, decode: () => bitmap(32, 16) });
    expect(doc.canUndo).toBe(false);
    doc.dispose();
    await expect(
      MarkupDocument.fromPng(blob, { width: 32, height: 16 }, { createCanvas, decode: () => bitmap(16, 32) })
    ).rejects.toMatchObject({ code: 'size' });
    await expect(
      MarkupDocument.fromPng(blob, { width: 32, height: 16 }, { createCanvas, decode: () => Promise.reject(new Error('bad')) })
    ).rejects.toMatchObject({ code: 'decode' });
  });

  it('releases canvases on dispose and refuses further use', () => {
    const { createCanvas, canvases } = harness();
    const doc = MarkupDocument.create(64, 64, { createCanvas });
    doc.commit(rect(5, 5, 40, 40));
    doc.dispose();
    doc.dispose();
    expect(canvases[0].width).toBe(0);
    expect(doc.isDisposed).toBe(true);
    expect(() => doc.commit(rect(1, 1, 9, 9))).toThrowError(expect.objectContaining({ code: 'disposed' }));
    expect(() => doc.layer).toThrow(MarkupDocumentError);
  });
});
