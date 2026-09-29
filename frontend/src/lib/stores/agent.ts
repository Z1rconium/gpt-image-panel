import { get, writable } from 'svelte/store';
import {
  cancelAgentTurn,
  createAgentConversation,
  deleteAgentConversation,
  getAgentConversation,
  listAgentConversations,
  openAgentTurnEvents,
  renameAgentConversation,
  startAgentTurn
} from '$lib/api/agent';
import { ApiError } from '$lib/api/client';
import type {
  AgentBlock,
  AgentConversationDetail,
  AgentConversationSummary,
  AgentImageParams,
  AgentImageRef,
  AgentMessage,
  AgentStreamEvent
} from '$lib/api/types/agent';
import { t } from '$lib/i18n';
import { applyAgentEvent, isTerminalAgentEvent } from '$lib/utils/agentBlocks';
import { isAbortError } from './assistant';

export type AgentState = {
  conversations: AgentConversationSummary[];
  listLoading: boolean;
  listError: string | null;
  activeId: string | null;
  messages: AgentMessage[];
  imageRefs: AgentImageRef[];
  hasMore: boolean;
  detailLoading: boolean;
  detailError: string | null;
  activeTurnId: string | null;
  sending: boolean;
  cancelling: boolean;
  actionError: string | null;
  /** Short status text for the screen-reader live region; never the reply itself. */
  announcement: string;
};

const initialState: AgentState = {
  conversations: [],
  listLoading: false,
  listError: null,
  activeId: null,
  messages: [],
  imageRefs: [],
  hasMore: false,
  detailLoading: false,
  detailError: null,
  activeTurnId: null,
  sending: false,
  cancelling: false,
  actionError: null,
  announcement: ''
};

export const AGENT_MAX_ATTACHMENTS = 8;
const IMAGE_SYNC_DEBOUNCE_MS = 350;
const RECOVER_DELAY_MS = 1500;
const MAX_RECOVER_ATTEMPTS = 5;

export type AgentSendInput = {
  text: string;
  attachmentIds: string[];
  imageParams: AgentImageParams;
};

export function newClientTurnId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `turn-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function blockToImageRef(block: Extract<AgentBlock, { type: 'image_task' }>, messageId: string): AgentImageRef {
  return {
    ref_label: block.ref_label,
    round_no: block.round_no,
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

function upsertRef(refs: AgentImageRef[], next: AgentImageRef): AgentImageRef[] {
  const index = refs.findIndex((ref) => ref.ref_label === next.ref_label);
  if (index === -1) return [...refs, next];
  const copy = refs.slice();
  copy[index] = next;
  return copy;
}

function createAgentStore() {
  const { subscribe, update } = writable<AgentState>(initialState);
  let state = initialState;
  subscribe((value) => {
    state = value;
  });

  let source: EventSource | null = null;
  let detailController: AbortController | null = null;
  let imageSyncHandler: (() => void) | null = null;
  let imageSyncTimer: ReturnType<typeof setTimeout> | null = null;
  let recoverTimer: ReturnType<typeof setTimeout> | null = null;
  let recoverAttempts = 0;

  function announce(message: string) {
    update((current) => ({ ...current, announcement: message }));
  }

  function closeSource() {
    if (source) source.close();
    source = null;
  }

  function scheduleImageSync() {
    if (!imageSyncHandler) return;
    if (imageSyncTimer) clearTimeout(imageSyncTimer);
    imageSyncTimer = setTimeout(() => {
      imageSyncTimer = null;
      imageSyncHandler?.();
    }, IMAGE_SYNC_DEBOUNCE_MS);
  }

  function applyDetail(detail: AgentConversationDetail) {
    update((current) => ({
      ...current,
      messages: detail.messages,
      imageRefs: detail.image_refs,
      hasMore: detail.has_more,
      detailLoading: false,
      detailError: null,
      conversations: current.conversations.some((item) => item.id === detail.conversation.id)
        ? current.conversations.map((item) => (item.id === detail.conversation.id ? detail.conversation : item))
        : current.conversations
    }));
  }

  async function loadList() {
    update((current) => ({ ...current, listLoading: true, listError: null }));
    try {
      const response = await listAgentConversations();
      update((current) => ({ ...current, conversations: response.items, listLoading: false }));
    } catch (error) {
      update((current) => ({
        ...current,
        listLoading: false,
        listError: errorMessage(error, get(t).agent.errorLoadList)
      }));
    }
  }

  /** Load a conversation's messages; returns null when it was superseded or failed. */
  async function refreshDetail(conversationId: string): Promise<AgentConversationDetail | null> {
    detailController?.abort();
    const controller = new AbortController();
    detailController = controller;
    try {
      const detail = await getAgentConversation(conversationId, controller.signal);
      if (state.activeId !== conversationId) return null;
      applyDetail(detail);
      return detail;
    } catch (error) {
      if (isAbortError(error) || state.activeId !== conversationId) return null;
      const missing = error instanceof ApiError && error.status === 404;
      update((current) => ({
        ...current,
        detailLoading: false,
        detailError: missing ? null : errorMessage(error, get(t).agent.errorLoadConversation),
        activeId: missing ? null : current.activeId
      }));
      if (missing) void loadList();
      return null;
    } finally {
      if (detailController === controller) detailController = null;
    }
  }

  function updateAssistantMessage(turnId: string, apply: (message: AgentMessage) => AgentMessage) {
    update((current) => ({
      ...current,
      messages: current.messages.map((message) =>
        message.turn_id === turnId && message.role === 'assistant' ? apply(message) : message
      )
    }));
  }

  function handleEvent(turnId: string, event: AgentStreamEvent) {
    const labels = get(t).agent;
    if (event.event === 'turn.started') {
      announce(labels.announceStarted);
      return;
    }
    if (event.event === 'block.upsert' || event.event === 'block.text') {
      let messageId = '';
      updateAssistantMessage(turnId, (message) => {
        messageId = message.id;
        return { ...message, blocks: applyAgentEvent(message.blocks, event) };
      });
      if (event.event === 'block.upsert' && event.data.block.type === 'image_task' && messageId) {
        const block = event.data.block;
        update((current) => ({ ...current, imageRefs: upsertRef(current.imageRefs, blockToImageRef(block, messageId)) }));
        if (block.status === 'succeeded') {
          announce(labels.announceImage(block.ref_label));
          scheduleImageSync();
        }
      }
      return;
    }
    if (isTerminalAgentEvent(event.event)) {
      closeSource();
      recoverAttempts = 0;
      announce(
        event.event === 'turn.completed'
          ? labels.announceCompleted
          : event.event === 'turn.cancelled'
            ? labels.announceCancelled
            : labels.announceFailed
      );
      update((current) => ({ ...current, activeTurnId: null, cancelling: false }));
      scheduleImageSync();
      const conversationId = state.activeId;
      if (conversationId) void refreshDetail(conversationId);
      void loadList();
    }
  }

  /** Follow a turn from its first event; the message is rebuilt from an empty state. */
  function attachStream(turnId: string) {
    closeSource();
    updateAssistantMessage(turnId, (message) => ({ ...message, blocks: [], status: 'streaming' }));
    update((current) => ({ ...current, activeTurnId: turnId }));
    source = openAgentTurnEvents(turnId, 0, {
      onEvent: (event) => handleEvent(turnId, event),
      onError: () => scheduleRecover(turnId),
      // EventSource reconnects by itself and resumes through Last-Event-ID.
      onNetworkError: () => {}
    });
  }

  function scheduleRecover(turnId: string) {
    closeSource();
    if (state.activeTurnId !== turnId || recoverAttempts >= MAX_RECOVER_ATTEMPTS) {
      update((current) => ({ ...current, activeTurnId: null, actionError: get(t).agent.errorLoadConversation }));
      return;
    }
    recoverAttempts += 1;
    if (recoverTimer) clearTimeout(recoverTimer);
    recoverTimer = setTimeout(() => {
      recoverTimer = null;
      const conversationId = state.activeId;
      if (conversationId && state.activeTurnId === turnId) void open(conversationId);
    }, RECOVER_DELAY_MS);
  }

  async function open(conversationId: string | null) {
    closeSource();
    if (recoverTimer) clearTimeout(recoverTimer);
    recoverTimer = null;
    if (!conversationId) {
      detailController?.abort();
      update((current) => ({
        ...current,
        activeId: null,
        messages: [],
        imageRefs: [],
        hasMore: false,
        detailLoading: false,
        detailError: null,
        activeTurnId: null,
        cancelling: false,
        actionError: null
      }));
      return;
    }
    const switching = state.activeId !== conversationId;
    update((current) => ({
      ...current,
      activeId: conversationId,
      detailLoading: true,
      detailError: null,
      actionError: null,
      ...(switching
        ? { messages: [], imageRefs: [], hasMore: false, activeTurnId: null, cancelling: false }
        : {})
    }));
    const detail = await refreshDetail(conversationId);
    if (detail?.active_turn) {
      attachStream(detail.active_turn.id);
    } else if (detail) {
      // The turn finished while we were away; do not stay in the running state.
      update((current) => ({ ...current, activeTurnId: null, cancelling: false }));
    }
  }

  async function create(title = ''): Promise<string | null> {
    try {
      const created = await createAgentConversation(title);
      update((current) => ({ ...current, conversations: [created, ...current.conversations] }));
      await open(created.id);
      return created.id;
    } catch (error) {
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorSend) }));
      return null;
    }
  }

  async function rename(conversationId: string, title: string): Promise<boolean> {
    try {
      const updated = await renameAgentConversation(conversationId, title);
      update((current) => ({
        ...current,
        conversations: current.conversations.map((item) => (item.id === updated.id ? updated : item))
      }));
      return true;
    } catch (error) {
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorRename) }));
      return false;
    }
  }

  async function remove(conversationId: string): Promise<boolean> {
    try {
      await deleteAgentConversation(conversationId);
    } catch (error) {
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorDelete) }));
      return false;
    }
    update((current) => ({
      ...current,
      conversations: current.conversations.filter((item) => item.id !== conversationId)
    }));
    if (state.activeId === conversationId) await open(null);
    return true;
  }

  async function send(input: AgentSendInput): Promise<boolean> {
    const text = input.text.trim();
    if (!text || state.sending || state.activeTurnId) return false;
    update((current) => ({ ...current, sending: true, actionError: null }));
    try {
      let conversationId = state.activeId;
      if (!conversationId) {
        const created = await createAgentConversation('');
        conversationId = created.id;
        update((current) => ({
          ...current,
          conversations: [created, ...current.conversations],
          activeId: created.id,
          messages: [],
          imageRefs: []
        }));
      }
      const accepted = await startAgentTurn(conversationId, {
        client_turn_id: newClientTurnId(),
        text,
        attachments: input.attachmentIds.slice(0, AGENT_MAX_ATTACHMENTS).map((imageId) => ({ kind: 'gallery', image_id: imageId })),
        image_params: input.imageParams
      });
      const detail = await refreshDetail(conversationId);
      if (detail) attachStream(accepted.turn_id);
      void loadList();
      return true;
    } catch (error) {
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorSend) }));
      return false;
    } finally {
      update((current) => ({ ...current, sending: false }));
    }
  }

  async function cancel() {
    const turnId = state.activeTurnId;
    if (!turnId || state.cancelling) return;
    update((current) => ({ ...current, cancelling: true, actionError: null }));
    try {
      await cancelAgentTurn(turnId);
    } catch (error) {
      update((current) => ({
        ...current,
        cancelling: false,
        actionError: errorMessage(error, get(t).agent.errorStop)
      }));
    }
  }

  async function loadEarlier() {
    const conversationId = state.activeId;
    const oldest = state.messages[0];
    if (!conversationId || !oldest || !state.hasMore) return;
    try {
      const response = await getAgentConversation(conversationId, undefined, oldest.seq);
      update((current) => ({
        ...current,
        messages: [...response.messages, ...current.messages],
        hasMore: response.has_more
      }));
    } catch (error) {
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorLoadConversation) }));
    }
  }

  function clearActionError() {
    update((current) => ({ ...current, actionError: null }));
  }

  function setImageSyncHandler(handler: (() => void) | null) {
    imageSyncHandler = handler;
  }

  /** Pause the stream while the tab is hidden and replay it when the tab returns. */
  function installVisibilityHandling(): () => void {
    if (typeof document === 'undefined') return () => {};
    const onChange = () => {
      if (document.hidden) {
        closeSource();
        return;
      }
      const turnId = state.activeTurnId;
      if (turnId && !source) attachStream(turnId);
    };
    document.addEventListener('visibilitychange', onChange);
    return () => document.removeEventListener('visibilitychange', onChange);
  }

  function dispose() {
    closeSource();
    detailController?.abort();
    if (imageSyncTimer) clearTimeout(imageSyncTimer);
    if (recoverTimer) clearTimeout(recoverTimer);
    imageSyncTimer = null;
    recoverTimer = null;
    imageSyncHandler = null;
  }

  return {
    subscribe,
    loadList,
    open,
    create,
    rename,
    remove,
    send,
    cancel,
    loadEarlier,
    clearActionError,
    setImageSyncHandler,
    installVisibilityHandling,
    dispose
  };
}

export const agentStore = createAgentStore();
