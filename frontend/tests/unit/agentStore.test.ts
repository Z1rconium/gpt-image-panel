import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { AgentConversationDetail, AgentMessage, AgentStreamEvent, AgentTurnStatusResponse } from '$lib/api/types/agent';

const api = vi.hoisted(() => ({
  cancelAgentTurn: vi.fn(),
  createAgentConversation: vi.fn(),
  deleteAgentConversation: vi.fn(),
  getAgentConversation: vi.fn(),
  getAgentTurn: vi.fn(),
  listAgentConversations: vi.fn(),
  openAgentTurnEvents: vi.fn(),
  renameAgentConversation: vi.fn(),
  startAgentTurn: vi.fn(),
  selectAgentBranch: vi.fn()
}));

const collections = vi.hoisted(() => ({ items: [] as { id: string; asset_alias: string }[], load: vi.fn() }));
vi.mock('$lib/stores/galleryCollections', () => ({ galleryCollectionsStore: {
  subscribe: (run: (value: unknown) => void) => { run({ collections: collections.items }); return () => {}; },
  load: collections.load
} }));
vi.mock('$lib/api/agent', () => api);
vi.mock('$lib/utils/completionNotifications', () => ({ observeAgentTurn: vi.fn() }));

import { agentStore } from '$lib/stores/agent';

type Handlers = { onEvent: (event: AgentStreamEvent, id: number) => void; onError?: () => void };
type Stream = { turnId: string; after: number; handlers: Handlers; close: ReturnType<typeof vi.fn> };

let streams: Stream[] = [];

function conversation(id: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    title: id,
    message_count: 0,
    turn_count: 0,
    created_at: '',
    updated_at: '',
    selected_turn_id: null,
    branch_revision: 0,
    ...extra
  };
}

function message(id: string, turnId: string, role: 'user' | 'assistant', seq: number, extra: Partial<AgentMessage> = {}): AgentMessage {
  return {
    id,
    turn_id: turnId,
    seq,
    round_no: 1,
    role,
    text: '',
    blocks: [],
    status: role === 'assistant' ? 'complete' : 'complete',
    created_at: '',
    updated_at: '',
    ...extra
  } as AgentMessage;
}

function detail(id: string, messages: AgentMessage[], extra: Partial<AgentConversationDetail> = {}): AgentConversationDetail {
  return {
    conversation: conversation(id),
    messages,
    image_refs: [],
    active_turn: null,
    has_more: false,
    branches: [],
    ...extra
  } as AgentConversationDetail;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

beforeEach(() => {
  vi.useFakeTimers();
  streams = [];
  collections.items = [];
  collections.load.mockReset().mockResolvedValue(undefined);
  for (const mock of Object.values(api)) mock.mockReset();
  api.listAgentConversations.mockResolvedValue({ items: [] });
  api.openAgentTurnEvents.mockImplementation((turnId: string, after: number, handlers: Handlers) => {
    const stream = { turnId, after, handlers, close: vi.fn() };
    streams.push(stream);
    return { close: stream.close } as unknown as EventSource;
  });
});

afterEach(() => {
  agentStore.dispose();
  vi.useRealTimers();
});

describe('agent store lifecycle', () => {
  it('ignores a late conversation response after switching to another conversation', async () => {
    const slow = deferred<AgentConversationDetail>();
    api.getAgentConversation.mockImplementation((id: string) =>
      id === 'a' ? slow.promise : Promise.resolve(detail('b', [message('mb', 't-b', 'user', 1)]))
    );
    const first = agentStore.open('a');
    await agentStore.open('b');
    slow.resolve(detail('a', [message('ma', 't-a', 'user', 1)]));
    await first;
    const state = get(agentStore);
    expect(state.activeId).toBe('b');
    expect(state.messages.map((item) => item.id)).toEqual(['mb']);
  });

  it('follows an active turn: merges stream blocks and settles on the terminal event', async () => {
    const running = detail('c', [message('u1', 't1', 'user', 1), message('a1', 't1', 'assistant', 2, { status: 'streaming' })], {
      active_turn: { id: 't1', status: 'running', round_no: 1 }
    });
    const finished = detail('c', [message('u1', 't1', 'user', 1), message('a1', 't1', 'assistant', 2, { text: 'Hello' })]);
    api.getAgentConversation.mockResolvedValueOnce(running).mockResolvedValue(finished);
    await agentStore.open('c');
    expect(streams).toHaveLength(1);
    expect(get(agentStore).activeTurnId).toBe('t1');

    const { handlers } = streams[0];
    handlers.onEvent({ event: 'block.upsert', data: { block: { id: 't1b', type: 'text', text: '' } } } as AgentStreamEvent, 1);
    handlers.onEvent({ event: 'block.text', data: { block_id: 't1b', delta: 'Hel' } } as AgentStreamEvent, 2);
    handlers.onEvent({ event: 'block.text', data: { block_id: 't1b', delta: 'lo' } } as AgentStreamEvent, 3);
    const live = get(agentStore).messages.find((item) => item.id === 'a1');
    expect(live?.blocks).toEqual([{ id: 't1b', type: 'text', text: 'Hello' }]);

    handlers.onEvent({ event: 'turn.completed', data: { rounds_used: 0 } } as AgentStreamEvent, 4);
    await vi.runAllTimersAsync();
    const state = get(agentStore);
    expect(state.activeTurnId).toBeNull();
    expect(streams[0].close).toHaveBeenCalled();
    expect(state.messages.find((item) => item.id === 'a1')?.text).toBe('Hello');
  });

  it('drops events from a stream that was already replaced', async () => {
    const running = detail('c', [message('u1', 't1', 'user', 1), message('a1', 't1', 'assistant', 2, { status: 'streaming' })], {
      active_turn: { id: 't1', status: 'running', round_no: 1 }
    });
    api.getAgentConversation.mockResolvedValue(running);
    await agentStore.open('c');
    const stale = streams[0].handlers;
    await agentStore.open('c');
    expect(streams).toHaveLength(2);
    stale.onEvent({ event: 'block.upsert', data: { block: { id: 'x', type: 'text', text: 'stale' } } } as AgentStreamEvent, 1);
    const blocks = get(agentStore).messages.find((item) => item.id === 'a1')?.blocks ?? [];
    expect(blocks.find((block) => block.id === 'x')).toBeUndefined();
  });

  it('loads earlier messages without duplicating and reopens when the branch moved', async () => {
    api.getAgentConversation.mockResolvedValueOnce(
      detail('c', [message('m3', 't3', 'user', 3), message('m4', 't3', 'assistant', 4)], { has_more: true })
    );
    await agentStore.open('c');
    api.getAgentConversation.mockResolvedValueOnce(
      detail('c', [message('m1', 't1', 'user', 1), message('m3', 't3', 'user', 3)], { has_more: false })
    );
    await agentStore.loadEarlier();
    expect(api.getAgentConversation).toHaveBeenLastCalledWith('c', undefined, 3);
    let state = get(agentStore);
    expect(state.messages.map((item) => item.id)).toEqual(['m1', 'm3', 'm4']);
    expect(state.hasMore).toBe(false);

    api.getAgentConversation.mockResolvedValueOnce(detail('c', [message('m1', 't1', 'user', 1)], { has_more: true }));
    await agentStore.open('c');
    api.getAgentConversation.mockResolvedValueOnce({
      ...detail('c', [message('m0', 't0', 'user', 0)]),
      conversation: conversation('c', { branch_revision: 9 })
    });
    api.getAgentConversation.mockResolvedValueOnce(detail('c', [message('fresh', 't9', 'user', 1)]));
    await agentStore.loadEarlier();
    state = get(agentStore);
    expect(state.messages.map((item) => item.id)).toEqual(['fresh']);
  });

  it('leaves the stopping state by polling when the stream never delivers a terminal event', async () => {
    const running = detail('c', [message('u1', 't1', 'user', 1), message('a1', 't1', 'assistant', 2, { status: 'streaming' })], {
      active_turn: { id: 't1', status: 'running', round_no: 1 }
    });
    api.getAgentConversation.mockResolvedValueOnce(running).mockResolvedValue(detail('c', [message('u1', 't1', 'user', 1)]));
    await agentStore.open('c');
    api.cancelAgentTurn.mockResolvedValue({});
    api.getAgentTurn.mockResolvedValue({ status: 'cancelled' } as AgentTurnStatusResponse);
    await agentStore.cancel();
    expect(get(agentStore).cancelling).toBe(true);
    await vi.advanceTimersByTimeAsync(1600);
    const state = get(agentStore);
    expect(state.cancelling).toBe(false);
    expect(state.activeTurnId).toBeNull();
  });

  it('recovers a broken stream by reopening, and gives up after bounded attempts', async () => {
    const running = detail('c', [message('u1', 't1', 'user', 1), message('a1', 't1', 'assistant', 2, { status: 'streaming' })], {
      active_turn: { id: 't1', status: 'running', round_no: 1 }
    });
    api.getAgentConversation.mockResolvedValue(running);
    await agentStore.open('c');
    for (let attempt = 0; attempt < 5; attempt += 1) {
      streams[streams.length - 1].handlers.onError?.();
      await vi.advanceTimersByTimeAsync(1600);
    }
    expect(streams).toHaveLength(6);
    streams[streams.length - 1].handlers.onError?.();
    await vi.advanceTimersByTimeAsync(1600);
    const state = get(agentStore);
    expect(state.activeTurnId).toBeNull();
    expect(state.messages.find((item) => item.id === 'a1')?.status).toBe('interrupted');
  });

  it('keeps the idempotency key across a retried send and sends the capability flag when set', async () => {
    api.createAgentConversation.mockResolvedValue(conversation('new'));
    api.startAgentTurn.mockRejectedValueOnce(new TypeError('network')).mockResolvedValue({ turn_id: 't9', replayed: true });
    api.getAgentConversation.mockResolvedValue(detail('new', []));
    const input = { text: 'draw', attachmentIds: [], imageParams: { size: 'auto', quality: 'auto', output_format: 'png' } as const, allowImageTools: true };
    expect(await agentStore.send(input)).toBe(false);
    expect(await agentStore.send(input)).toBe(true);
    const [first, second] = api.startAgentTurn.mock.calls.map((call) => call[1]);
    expect(first.client_turn_id).toBe(second.client_turn_id);
    expect(second.allow_image_tools).toBe(true);
  });
  it('edits asset mentions using retained snapshots and expands only newly mentioned packs', async () => {
    collections.items = [{ id: 'kept', asset_alias: 'hero' }, { id: 'removed', asset_alias: 'old' }, { id: 'new', asset_alias: 'new' }];
    const user = message('u', 't', 'user', 1, { text: '@hero @old', blocks: [
      { id: 'b1', type: 'asset_refs', alias: 'hero', collection_id: 'kept', refs: ['round-1-input-1'] },
      { id: 'b2', type: 'asset_refs', alias: 'old', collection_id: 'removed', refs: ['round-1-input-2', 'round-1-input-4'], explicit_refs: ['round-1-input-4'] }
    ] });
    const refs = [1, 2, 3, 4].map((index) => ({ ref_label: `round-1-input-${index}`, round_no: 1, image_index: index, role: 'input', image_id: `g${index}`, filename: `g${index}.png`, job_id: null, item_id: null, prompt: '', mode: 'generate', status: 'succeeded', error: null, deleted: false, message_id: 'u' })) as AgentConversationDetail['image_refs'];
    api.getAgentConversation.mockResolvedValue(detail('c', [user], { image_refs: refs }));
    api.startAgentTurn.mockResolvedValue({ turn_id: 'next', replayed: true });
    await agentStore.open('c');
    expect(await agentStore.fork(user, '@HERO @new')).toBe(true);
    expect(api.startAgentTurn.mock.calls[0][1].attachments).toEqual([
      { kind: 'gallery', image_id: 'g1' }, { kind: 'gallery', image_id: 'g3' }, { kind: 'gallery', image_id: 'g4' }, { kind: 'asset', collection_id: 'new' }
    ]);
  });

  it('edits an ordinary message without loading collections even when that endpoint fails', async () => {
    collections.load.mockRejectedValue(new Error('gallery unavailable'));
    const user = message('u', 't', 'user', 1, { text: 'hello' });
    api.getAgentConversation.mockResolvedValue(detail('c', [user]));
    api.startAgentTurn.mockResolvedValue({ turn_id: 'next', replayed: true });
    await agentStore.open('c');
    expect(await agentStore.fork(user, 'revised @round-1-image-1')).toBe(true);
    expect(collections.load).not.toHaveBeenCalled();
    expect(api.startAgentTurn.mock.calls[0][1].attachments).toEqual([]);
  });

});
