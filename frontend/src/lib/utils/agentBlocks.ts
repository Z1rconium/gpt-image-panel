import type {
  AgentBatchParamsBlock,
  AgentBlock,
  AgentErrorBlock,
  AgentImageTaskBlock,
  AgentStreamEvent,
  AgentStreamEventName,
  AgentTextBlock
} from '$lib/api/types/agent';

const TERMINAL_EVENTS: ReadonlySet<AgentStreamEventName> = new Set(['turn.completed', 'turn.failed', 'turn.cancelled']);

export function isTerminalAgentEvent(name: AgentStreamEventName): boolean {
  return TERMINAL_EVENTS.has(name);
}

export function upsertAgentBlock(blocks: AgentBlock[], block: AgentBlock): AgentBlock[] {
  const index = blocks.findIndex((existing) => existing.id === block.id);
  if (index === -1) return [...blocks, block];
  const next = blocks.slice();
  next[index] = block;
  return next;
}

/**
 * Fold one stream event into a message's blocks. Applying the same events in
 * order always yields what the server stored, so a replay from the start
 * rebuilds the message exactly.
 */
export function applyAgentEvent(blocks: AgentBlock[], event: AgentStreamEvent): AgentBlock[] {
  if (event.event === 'block.upsert') {
    return upsertAgentBlock(blocks, event.data.block);
  }
  if (event.event === 'block.text') {
    const { block_id: blockId, delta } = event.data;
    const index = blocks.findIndex((block) => block.id === blockId && block.type === 'text');
    if (index === -1) {
      return [...blocks, { id: blockId, type: 'text', text: delta }];
    }
    const target = blocks[index];
    if (target.type !== 'text') return blocks;
    const next = blocks.slice();
    next[index] = { ...target, text: target.text + delta };
    return next;
  }
  return blocks;
}

/** Text of the visible reply, used for empty-state checks and announcements. */
export function agentBlocksText(blocks: AgentBlock[]): string {
  return blocks
    .filter((block): block is Extract<AgentBlock, { type: 'text' }> => block.type === 'text')
    .map((block) => block.text)
    .join('\n\n')
    .trim();
}

export type AgentBlockGroup =
  | { kind: 'text'; key: string; block: AgentTextBlock }
  | { kind: 'batch'; key: string; block: AgentBatchParamsBlock }
  | { kind: 'images'; key: string; blocks: AgentImageTaskBlock[] }
  | { kind: 'error'; key: string; block: AgentErrorBlock };

/** Consecutive image tasks render as one grid; everything else stays one block per row. */
export function groupAgentBlocks(blocks: AgentBlock[]): AgentBlockGroup[] {
  const groups: AgentBlockGroup[] = [];
  for (const block of blocks) {
    if (block.type === 'image_task') {
      const last = groups[groups.length - 1];
      if (last?.kind === 'images') {
        last.blocks.push(block);
      } else {
        groups.push({ kind: 'images', key: block.id, blocks: [block] });
      }
    } else if (block.type === 'text') {
      if (block.text.trim()) groups.push({ kind: 'text', key: block.id, block });
    } else if (block.type === 'batch_params') {
      groups.push({ kind: 'batch', key: block.id, block });
    } else {
      groups.push({ kind: 'error', key: block.id, block });
    }
  }
  return groups;
}
