/**
 * Owns the live connection to one running turn: the EventSource, the request
 * epoch that invalidates callbacks of a replaced connection, the replay cursor,
 * and the per-frame batching of incoming events. It knows nothing about the
 * store's state; interpretation happens in the `onBatch` callback.
 */
import { openAgentTurnEvents } from '$lib/api/agent';
import type { PendingAgentEvent } from './agentState';

export type AgentStreamHandlers = {
  /** Events for the attached turn, already ordered, delivered once per animation frame. */
  onBatch: (turnId: string, batch: PendingAgentEvent[]) => void;
  /** The connection failed in a way EventSource will not recover from by itself. */
  onBroken: (turnId: string) => void;
};

export function createAgentStreamController(handlers: AgentStreamHandlers) {
  let source: EventSource | null = null;
  let epoch = 0;
  let turnId: string | null = null;
  let lastSeq = 0;
  let pending: PendingAgentEvent[] = [];
  let frame: number | null = null;

  function cancelFrame() {
    if (frame !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame);
    frame = null;
  }

  /** Deliver everything buffered so far in one batch. */
  function flush() {
    cancelFrame();
    if (!pending.length || !turnId) {
      pending = [];
      return;
    }
    const batch = pending;
    pending = [];
    for (const entry of batch) if (entry.seq > lastSeq) lastSeq = entry.seq;
    handlers.onBatch(turnId, batch);
  }

  function enqueue(forTurn: string, entry: PendingAgentEvent) {
    if (turnId !== forTurn) return;
    pending.push(entry);
    if (typeof requestAnimationFrame !== 'function') {
      flush();
      return;
    }
    if (frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      flush();
    });
  }

  /** Drop the connection; callbacks of the old one become no-ops. */
  function close() {
    epoch += 1;
    if (source) source.close();
    source = null;
    turnId = null;
  }

  /** Follow `id`, replaying from the last applied event unless `fresh` resets the cursor. */
  function attach(id: string, fresh: boolean) {
    flush();
    close();
    if (fresh) lastSeq = 0;
    turnId = id;
    const mine = epoch;
    source = openAgentTurnEvents(id, lastSeq, {
      onEvent: (event, eventId) => {
        if (mine === epoch) enqueue(id, { event, seq: eventId });
      },
      onError: () => {
        if (mine === epoch) handlers.onBroken(id);
      },
      // EventSource reconnects by itself and resumes through Last-Event-ID.
      onNetworkError: () => {}
    });
  }

  function dispose() {
    flush();
    close();
    cancelFrame();
    pending = [];
  }

  return {
    attach,
    close,
    flush,
    dispose,
    get connected() {
      return source !== null;
    }
  };
}
