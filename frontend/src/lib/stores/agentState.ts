/**
 * Pure state and reducers of the Agent store. Nothing here touches the
 * network, timers or EventSource, so each transition can be tested on its own.
 */
import type {
  AgentBlock,
  AgentBranch,
  AgentConversationDetail,
  AgentConversationSummary,
  AgentImageRef,
  AgentMessage,
  AgentStreamEvent
} from '$lib/api/types/agent';
import { applyAgentEvent } from '$lib/utils/agentBlocks';

export type AgentState = {
  conversations: AgentConversationSummary[];
  listLoading: boolean;
  listError: string | null;
  activeId: string | null;
  messages: AgentMessage[];
  imageRefs: AgentImageRef[];
  hasMore: boolean;
  branches: AgentBranch[];
  selectedTurnId: string | null;
  branchRevision: number;
  detailLoading: boolean;
  detailError: string | null;
  activeTurnId: string | null;
  sending: boolean;
  cancelling: boolean;
  actionError: string | null;
  /** Short status text for the screen-reader live region; never the reply itself. */
  announcement: string;
};

export const initialAgentState: AgentState = {
  conversations: [],
  listLoading: false,
  listError: null,
  activeId: null,
  messages: [],
  imageRefs: [],
  hasMore: false,
  branches: [],
  selectedTurnId: null,
  branchRevision: 0,
  detailLoading: false,
  detailError: null,
  activeTurnId: null,
  sending: false,
  cancelling: false,
  actionError: null,
  announcement: ''
};

export type TerminalEventName = 'turn.completed' | 'turn.failed' | 'turn.cancelled';
export type PendingAgentEvent = { event: AgentStreamEvent; seq: number };

export function blockToImageRef(block: Extract<AgentBlock, { type: 'image_task' }>, messageId: string): AgentImageRef {
  return {
    ref_label: block.ref_label,
    round_no: block.round_no,
    path_round_no: block.path_round_no,
    image_index: block.image_index,
    role: 'output',
    image_id: block.image_id,
    filename: block.filename,
    job_id: block.job_id,
    item_id: block.item_id,
    prompt: block.prompt,
    mode: block.mode,
    status: block.status,
    error: block.error,
    deleted: Boolean(block.deleted),
    message_id: messageId
  };
}

export function upsertRef(refs: AgentImageRef[], next: AgentImageRef): AgentImageRef[] {
  const index = refs.findIndex((ref) => ref.ref_label === next.ref_label);
  if (index === -1) return [...refs, next];
  const copy = refs.slice();
  copy[index] = next;
  return copy;
}

/** A freshly loaded conversation replaces the visible page of messages. */
export function withDetail(current: AgentState, detail: AgentConversationDetail): AgentState {
  return {
    ...current,
    messages: detail.messages,
    imageRefs: detail.image_refs,
    hasMore: detail.has_more,
    branches: detail.branches ?? [],
    selectedTurnId: detail.conversation.selected_turn_id ?? null,
    branchRevision: detail.conversation.branch_revision ?? 0,
    detailLoading: false,
    detailError: null,
    conversations: current.conversations.some((item) => item.id === detail.conversation.id)
      ? current.conversations.map((item) => (item.id === detail.conversation.id ? detail.conversation : item))
      : current.conversations
  };
}

/** Prepend an older page, keeping the already loaded copy of any overlapping message or ref. */
export function withEarlierPage(current: AgentState, page: AgentConversationDetail): AgentState {
  return {
    ...current,
    messages: [
      ...page.messages,
      ...current.messages.filter((message) => !page.messages.some((earlier) => earlier.id === message.id))
    ],
    imageRefs: [
      ...page.image_refs,
      ...current.imageRefs.filter((ref) => !page.image_refs.some((earlier) => earlier.ref_label === ref.ref_label))
    ],
    hasMore: page.has_more
  };
}

export function withAssistantMessage(
  current: AgentState,
  turnId: string,
  apply: (message: AgentMessage) => AgentMessage
): AgentState {
  return {
    ...current,
    messages: current.messages.map((message) =>
      message.turn_id === turnId && message.role === 'assistant' ? apply(message) : message
    )
  };
}

export type BatchFold = {
  state: AgentState;
  started: boolean;
  terminal: TerminalEventName | null;
  /** Labels of images that finished in this batch, in event order. */
  succeededLabels: string[];
};

/**
 * Fold a burst of stream events for `turnId` into the state in one pass: block
 * events update the assistant message and the image refs; lifecycle events are
 * reported so the caller can announce them and settle the turn.
 */
export function foldStreamBatch(current: AgentState, turnId: string, batch: PendingAgentEvent[]): BatchFold {
  let started = false;
  let terminal: TerminalEventName | null = null;
  const blockEvents: PendingAgentEvent[] = [];
  for (const entry of batch) {
    const name = entry.event.event;
    if (name === 'turn.started') started = true;
    if (name === 'turn.completed' || name === 'turn.failed' || name === 'turn.cancelled') terminal = name;
    if (name === 'block.upsert' || name === 'block.text') blockEvents.push(entry);
  }
  let next = current;
  const succeededLabels: string[] = [];
  if (blockEvents.length) {
    let messageId = '';
    const refUpdates: AgentImageRef[] = [];
    next = withAssistantMessage(next, turnId, (message) => {
      messageId = message.id;
      let blocks = message.blocks;
      for (const entry of blockEvents) {
        blocks = applyAgentEvent(blocks, entry.event);
        if (entry.event.event === 'block.upsert' && entry.event.data.block.type === 'image_task') {
          refUpdates.push(blockToImageRef(entry.event.data.block, messageId));
        }
      }
      return { ...message, blocks };
    });
    for (const ref of refUpdates) {
      next = { ...next, imageRefs: upsertRef(next.imageRefs, ref) };
      if (ref.status === 'succeeded') succeededLabels.push(ref.ref_label);
    }
  }
  return { state: next, started, terminal, succeededLabels };
}
