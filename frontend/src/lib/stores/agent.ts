import { get, writable } from 'svelte/store';
import {
  cancelAgentTurn,
  createAgentConversation,
  deleteAgentConversation,
  getAgentConversation,
  getAgentTurn,
  listAgentConversations,
  renameAgentConversation,
  startAgentTurn,
  selectAgentBranch
} from '$lib/api/agent';
import { ApiError } from '$lib/api/client';
import type {
  AgentConversationDetail,
  AgentImageParams,
  AgentMessage,
  AgentTurnStatusResponse
} from '$lib/api/types/agent';
import { t } from '$lib/i18n';
import { observeAgentTurn } from '$lib/utils/completionNotifications';
import { isAbortError } from './assistant';
import {
  foldStreamBatch,
  initialAgentState,
  withAssistantMessage,
  withDetail,
  withEarlierPage,
  type AgentState,
  type PendingAgentEvent,
  type TerminalEventName
} from './agentState';
import { createAgentStreamController } from './agentStream';

export type { AgentState } from './agentState';

export const AGENT_MAX_ATTACHMENTS = 8;
const IMAGE_SYNC_DEBOUNCE_MS = 350;
const RECOVER_DELAY_MS = 1500;
const MAX_RECOVER_ATTEMPTS = 5;
const CANCEL_POLL_INTERVAL_MS = 1500;
const CANCEL_POLL_ATTEMPTS = 10;

export type AgentSendInput = {
  text: string;
  action?: 'continue' | 'edit' | 'regenerate';
  sourceTurnId?: string;
  attachmentIds: string[];
  imageParams: AgentImageParams;
  allowImageTools?: boolean;
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

function createAgentStore() {
  const { subscribe, update } = writable<AgentState>(initialAgentState);
  let state = initialAgentState;
  subscribe((value) => {
    state = value;
  });

  let detailController: AbortController | null = null;
  let imageSyncHandler: (() => void) | null = null;
  let imageSyncTimer: ReturnType<typeof setTimeout> | null = null;
  let recoverTimer: ReturnType<typeof setTimeout> | null = null;
  let cancelPollTimer: ReturnType<typeof setTimeout> | null = null;
  let recoverAttempts = 0;
  let pendingSubmission: { conversationId: string; fingerprint: string; clientTurnId: string; revision: number } | null = null;

  function announce(message: string) {
    update((current) => ({ ...current, announcement: message }));
  }

  const stream = createAgentStreamController({
    onBatch: (turnId, batch) => handleStreamBatch(turnId, batch),
    onBroken: (turnId) => scheduleRecover(turnId)
  });

  function clearCancelPoll() {
    if (cancelPollTimer) clearTimeout(cancelPollTimer);
    cancelPollTimer = null;
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
    for (const message of detail.messages) {
      if (message.role === 'assistant') observeCompletion(message, detail.conversation.id);
    }
    update((current) => withDetail(current, detail));
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
    update((current) => withAssistantMessage(current, turnId, apply));
  }

  function finishTurn(eventName: TerminalEventName) {
    const turnId = state.activeTurnId;
    const message = state.messages.find((item) => item.turn_id === turnId && item.role === 'assistant');
    if (message && state.activeId) observeCompletion({
      ...message, status: eventName === 'turn.completed' ? 'complete' : eventName === 'turn.failed' ? 'failed' : 'cancelled'
    }, state.activeId);
    clearCancelPoll();
    stream.close();
    recoverAttempts = 0;
    const labels = get(t).agent;
    announce(
      eventName === 'turn.completed'
        ? labels.announceCompleted
        : eventName === 'turn.cancelled'
          ? labels.announceCancelled
          : labels.announceFailed
    );
    update((current) => ({ ...current, activeTurnId: null, cancelling: false }));
    scheduleImageSync();
    const conversationId = state.activeId;
    if (conversationId) void refreshDetail(conversationId);
    void loadList();
  }

  function observeCompletion(message: AgentMessage, conversationId: string) {
    const images = message.blocks.filter((block) => block.type === 'image_task');
    void observeAgentTurn({
      turnId: message.turn_id, conversationId,
      status: message.status === 'streaming' ? 'running' : message.status === 'complete' ? 'completed' : message.status,
      successCount: images.filter((block) => block.status === 'succeeded').length,
      failureCount: images.filter((block) => block.status === 'failed' || block.status === 'cancelled').length
    });
  }

  /** A burst of stream frames costs one store notification instead of one per frame. */
  function handleStreamBatch(turnId: string, batch: PendingAgentEvent[]) {
    const labels = get(t).agent;
    let result!: ReturnType<typeof foldStreamBatch>;
    update((current) => {
      result = foldStreamBatch(current, turnId, batch);
      return result.state;
    });
    for (const label of result.succeededLabels) {
      announce(labels.announceImage(label));
      scheduleImageSync();
    }
    if (result.started) announce(labels.announceStarted);
    if (result.terminal) finishTurn(result.terminal);
  }

  /**
   * Follow a turn. `resume` keeps the already-rendered blocks and replays from
   * the last applied event id; a fresh attach rebuilds the message from scratch.
   */
  function attachStream(turnId: string, resume = false) {
    stream.flush();
    stream.close();
    if (!resume) updateAssistantMessage(turnId, (message) => ({ ...message, blocks: [], status: 'streaming' }));
    if (state.activeId) void observeAgentTurn({ turnId, conversationId: state.activeId, status: 'running', successCount: 0, failureCount: 0 });
    update((current) => ({ ...current, activeTurnId: turnId }));
    stream.attach(turnId, !resume);
  }

  function scheduleRecover(turnId: string) {
    stream.flush();
    stream.close();
    if (state.activeTurnId !== turnId) return;
    if (recoverAttempts >= MAX_RECOVER_ATTEMPTS) {
      updateAssistantMessage(turnId, (message) => ({ ...message, status: 'interrupted' }));
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
    if (conversationId !== state.activeId) pendingSubmission = null;
    stream.flush();
    stream.close();
    clearCancelPoll();
    if (recoverTimer) clearTimeout(recoverTimer);
    recoverTimer = null;
    if (!conversationId) {
      detailController?.abort();
      update((current) => ({
        ...current,
        activeId: null,
        branches: [], selectedTurnId: null, branchRevision: 0,
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
    if (switching) recoverAttempts = 0;
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
    if (detail?.active_turn && detail.messages.some((message) => message.turn_id === detail.active_turn?.id)) {
      attachStream(detail.active_turn.id);
    } else if (detail) {
      // The turn finished while we were away; do not stay in the running state.
      update((current) => ({ ...current, activeTurnId: detail.active_turn?.id ?? null, cancelling: false }));
      if (detail.active_turn) pollTurnAfterCancel(detail.active_turn.id);
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
          branches: [], selectedTurnId: null, branchRevision: created.branch_revision ?? 0,
          messages: [],
          imageRefs: []
        }));
      }
      const fingerprint = JSON.stringify({ ...input, text });
      if (pendingSubmission?.conversationId !== conversationId || pendingSubmission.fingerprint !== fingerprint) {
        pendingSubmission = { conversationId, fingerprint, clientTurnId: newClientTurnId(), revision: state.branchRevision };
      }
      const accepted = await startAgentTurn(conversationId, {
        client_turn_id: pendingSubmission.clientTurnId,
        text,
        action: input.action ?? 'continue',
        source_turn_id: input.sourceTurnId,
        branch_revision: pendingSubmission.revision,
        attachments: input.attachmentIds.slice(0, AGENT_MAX_ATTACHMENTS).map((imageId) => ({ kind: 'gallery', image_id: imageId })),
        image_params: input.imageParams,
        ...(input.allowImageTools === undefined ? {} : { allow_image_tools: input.allowImageTools })
      });
      pendingSubmission = null;
      if (!accepted.replayed) void observeAgentTurn({ turnId: accepted.turn_id, conversationId, status: 'queued', successCount: 0, failureCount: 0 });
      const detail = await refreshDetail(conversationId);
      if (detail?.messages.some((message) => message.turn_id === accepted.turn_id)) attachStream(accepted.turn_id);
      void loadList();
      return true;
    } catch (error) {
      // Transport failures may arrive after the server accepted the turn.
      // Retain its key and revision until a definitive response or input change.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) pendingSubmission = null;
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorSend) }));
      if (error instanceof ApiError && error.status === 409 && state.activeId) await refreshDetail(state.activeId);
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
      pollTurnAfterCancel(turnId);
    } catch (error) {
      update((current) => ({
        ...current,
        cancelling: false,
        actionError: errorMessage(error, get(t).agent.errorStop)
      }));
    }
  }

  /**
   * The cancel POST can succeed without the stream delivering a terminal event
   * (dead socket, exhausted recover). Poll the turn status so the UI leaves the
   * "stopping" state instead of hanging on it.
   */
  function pollTurnAfterCancel(turnId: string) {
    clearCancelPoll();
    let attempts = 0;
    const tick = async () => {
      cancelPollTimer = null;
      if (state.activeTurnId !== turnId) return;
      attempts += 1;
      let status: AgentTurnStatusResponse | null = null;
      try {
        status = await getAgentTurn(turnId);
      } catch {
        status = null;
      }
      if (state.activeTurnId !== turnId) return;
      if (status && status.status !== 'queued' && status.status !== 'running') {
        if (status.status === 'completed') {
          finishTurn('turn.completed');
        } else if (status.status === 'cancelled') {
          finishTurn('turn.cancelled');
        } else if (status.status === 'failed') {
          finishTurn('turn.failed');
        } else {
          // Interrupted: the server stopped the turn without a stream terminal.
          updateAssistantMessage(turnId, (message) => ({ ...message, status: 'interrupted' }));
          update((current) => ({ ...current, activeTurnId: null, cancelling: false }));
          const conversationId = state.activeId;
          if (conversationId) void refreshDetail(conversationId);
        }
        return;
      }
      if (attempts >= CANCEL_POLL_ATTEMPTS && !status) {
        updateAssistantMessage(turnId, (message) => ({ ...message, status: 'interrupted' }));
        update((current) => ({ ...current, activeTurnId: null, cancelling: false }));
        const conversationId = state.activeId;
        if (conversationId) void refreshDetail(conversationId);
        return;
      }
      cancelPollTimer = setTimeout(() => void tick(), CANCEL_POLL_INTERVAL_MS);
    };
    cancelPollTimer = setTimeout(() => void tick(), CANCEL_POLL_INTERVAL_MS);
  }

  async function loadEarlier() {
    const conversationId = state.activeId;
    const oldest = state.messages[0];
    if (!conversationId || !oldest || !state.hasMore) return;
    try {
      const response = await getAgentConversation(conversationId, undefined, oldest.seq);
      if (state.activeId !== conversationId) return;
      if (response.conversation.branch_revision !== state.branchRevision) {
        await open(conversationId);
        return;
      }
      update((current) => withEarlierPage(current, response));
    } catch (error) {
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorLoadConversation) }));
    }
  }

  async function selectBranch(turnId: string | null) {
    const conversationId = state.activeId;
    if (!conversationId || state.sending) return;
    pendingSubmission = null;
    update((current) => ({ ...current, sending: true, actionError: null }));
    stream.flush();
    stream.close();
    detailController?.abort();
    try {
      await selectAgentBranch(conversationId, turnId, state.branchRevision);
      if (state.activeId === conversationId) await open(conversationId);
    } catch (error) {
      if (state.activeId === conversationId) await open(conversationId);
      update((current) => ({ ...current, actionError: errorMessage(error, get(t).agent.errorLoadConversation) }));
    } finally {
      update((current) => ({ ...current, sending: false }));
    }
  }

  async function fork(message: AgentMessage, text?: string) {
    const inputs = state.imageRefs.filter((ref) => ref.message_id === message.id && ref.role === 'input');
    return send({
      text: text ?? state.messages.find((item) => item.turn_id === message.turn_id && item.role === 'user')?.text ?? 'Regenerate',
      action: text === undefined ? 'regenerate' : 'edit',
      sourceTurnId: message.turn_id,
      attachmentIds: inputs.flatMap((ref) => ref.image_id ? [ref.image_id] : []),
      imageParams: { size: 'auto', quality: 'auto', output_format: 'png' }
    });
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
        stream.flush();
        stream.close();
        return;
      }
      const turnId = state.activeTurnId;
      if (turnId && !stream.connected && state.messages.some((message) => message.turn_id === turnId)) attachStream(turnId, true);
    };
    document.addEventListener('visibilitychange', onChange);
    return () => document.removeEventListener('visibilitychange', onChange);
  }

  function dispose() {
    stream.dispose();
    clearCancelPoll();
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
    selectBranch,
    fork,
    cancel,
    loadEarlier,
    clearActionError,
    setImageSyncHandler,
    installVisibilityHandling,
    dispose
  };
}

export const agentStore = createAgentStore();
