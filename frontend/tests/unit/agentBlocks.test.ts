import { describe, expect, it } from 'vitest';
import type { AgentBlock, AgentImageTaskBlock, AgentStreamEvent } from '$lib/api/types/agent';
import { agentBlocksText, applyAgentEvent, isTerminalAgentEvent, upsertAgentBlock } from '$lib/utils/agentBlocks';

function task(overrides: Partial<AgentImageTaskBlock> = {}): AgentImageTaskBlock {
  return {
    id: 'i1',
    type: 'image_task',
    call_id: 'c1',
    item_id: 'fox',
    ref_label: 'round-1-image-1',
    round_no: 1,
    image_index: 1,
    job_id: null,
    prompt: 'a fox',
    mode: 'generate',
    source_refs: [],
    status: 'pending',
    stage: 'queued',
    error: null,
    image_id: null,
    filename: null,
    ...overrides
  };
}

function fold(events: AgentStreamEvent[], start: AgentBlock[] = []): AgentBlock[] {
  return events.reduce((blocks, event) => applyAgentEvent(blocks, event), start);
}

describe('applyAgentEvent', () => {
  it('creates a text block and appends deltas to it', () => {
    const blocks = fold([
      { event: 'block.upsert', data: { block: { id: 't1', type: 'text', text: '' } } },
      { event: 'block.text', data: { block_id: 't1', delta: 'Hel' } },
      { event: 'block.text', data: { block_id: 't1', delta: 'lo' } }
    ]);
    expect(blocks).toEqual([{ id: 't1', type: 'text', text: 'Hello' }]);
    expect(agentBlocksText(blocks)).toBe('Hello');
  });

  it('replaces an image task in place as it progresses', () => {
    const blocks = fold([
      { event: 'block.upsert', data: { block: task() } },
      { event: 'block.upsert', data: { block: { id: 't2', type: 'text', text: 'after' } } },
      { event: 'block.upsert', data: { block: task({ status: 'succeeded', image_id: 'g1', filename: 'g1.png' }) } }
    ]);
    expect(blocks.map((block) => block.id)).toEqual(['i1', 't2']);
    expect(blocks[0]).toMatchObject({ status: 'succeeded', image_id: 'g1' });
  });

  it('recovers a text delta whose block was never announced and ignores lifecycle events', () => {
    const blocks = fold([{ event: 'block.text', data: { block_id: 'tx', delta: 'orphan' } }]);
    expect(blocks).toEqual([{ id: 'tx', type: 'text', text: 'orphan' }]);
    const start: AgentBlock[] = [{ id: 'tx', type: 'text', text: 'x' }];
    expect(applyAgentEvent(start, { event: 'turn.completed', data: { rounds_used: 1 } })).toBe(start);
    expect(applyAgentEvent(start, { event: 'turn.failed', data: { message: 'boom' } })).toBe(start);
  });

  it('does not mutate the input and replays to the same result', () => {
    const events: AgentStreamEvent[] = [
      { event: 'block.upsert', data: { block: { id: 't1', type: 'text', text: '' } } },
      { event: 'block.text', data: { block_id: 't1', delta: 'a' } },
      { event: 'block.upsert', data: { block: task() } }
    ];
    const first = fold(events);
    const snapshot = JSON.stringify(first);
    expect(JSON.stringify(fold(events))).toBe(snapshot);
    const before: AgentBlock[] = [{ id: 't1', type: 'text', text: 'keep' }];
    upsertAgentBlock(before, { id: 't1', type: 'text', text: 'changed' });
    applyAgentEvent(before, { event: 'block.text', data: { block_id: 't1', delta: '!' } });
    expect(before).toEqual([{ id: 't1', type: 'text', text: 'keep' }]);
  });
});

describe('isTerminalAgentEvent', () => {
  it('flags only the events that end a turn', () => {
    expect(['turn.completed', 'turn.failed', 'turn.cancelled'].every((name) => isTerminalAgentEvent(name as never))).toBe(true);
    expect(['turn.started', 'block.upsert', 'block.text'].some((name) => isTerminalAgentEvent(name as never))).toBe(false);
  });
});
