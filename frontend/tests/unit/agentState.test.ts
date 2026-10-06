import { describe, expect, it } from 'vitest';
import type { AgentConversationDetail, AgentMessage, AgentStreamEvent } from '$lib/api/types/agent';
import { foldStreamBatch, initialAgentState, withEarlierPage, type AgentState } from '$lib/stores/agentState';

function assistant(id: string, turnId: string, seq: number): AgentMessage {
  return { id, turn_id: turnId, seq, round_no: 1, role: 'assistant', text: '', blocks: [], status: 'streaming', created_at: '', updated_at: '' } as AgentMessage;
}

function imageTask(status: string) {
  return {
    id: 'i1', type: 'image_task', call_id: 'c', item_id: 'a', ref_label: 'round-1-image-1', round_no: 1, image_index: 1,
    job_id: 'j', prompt: 'p', mode: 'generate', source_refs: [], status, stage: 'queued', error: null, image_id: null, filename: null
  };
}

describe('foldStreamBatch', () => {
  const base: AgentState = { ...initialAgentState, messages: [assistant('a1', 't1', 2)] };

  it('applies block events, mirrors image tasks into refs and reports lifecycle events', () => {
    const batch = [
      { seq: 1, event: { event: 'turn.started', data: {} } as AgentStreamEvent },
      { seq: 2, event: { event: 'block.upsert', data: { block: imageTask('pending') } } as AgentStreamEvent },
      { seq: 3, event: { event: 'block.upsert', data: { block: imageTask('succeeded') } } as AgentStreamEvent },
      { seq: 4, event: { event: 'turn.completed', data: { rounds_used: 1 } } as AgentStreamEvent }
    ];
    const result = foldStreamBatch(base, 't1', batch);
    expect(result.started).toBe(true);
    expect(result.terminal).toBe('turn.completed');
    expect(result.succeededLabels).toEqual(['round-1-image-1']);
    expect(result.state.messages[0].blocks).toHaveLength(1);
    expect(result.state.imageRefs).toHaveLength(1);
    expect(result.state.imageRefs[0].status).toBe('succeeded');
  });

  it('leaves messages of other turns untouched and never mutates its input', () => {
    const other: AgentState = { ...base, messages: [...base.messages, assistant('a2', 't2', 4)] };
    const result = foldStreamBatch(other, 't1', [
      { seq: 1, event: { event: 'block.upsert', data: { block: { id: 't', type: 'text', text: 'x' } } } as AgentStreamEvent }
    ]);
    expect(result.state.messages[1]).toBe(other.messages[1]);
    expect(other.messages[0].blocks).toEqual([]);
  });
});

describe('withEarlierPage', () => {
  it('prepends the page and keeps the already loaded copy of overlaps', () => {
    const loaded = assistant('m3', 't3', 3);
    const page = { messages: [assistant('m1', 't1', 1), { ...loaded, text: 'stale' }], image_refs: [], has_more: false } as unknown as AgentConversationDetail;
    const next = withEarlierPage({ ...initialAgentState, messages: [loaded], hasMore: true }, page);
    expect(next.messages.map((message) => message.id)).toEqual(['m1', 'm3']);
    expect(next.hasMore).toBe(false);
  });
});
