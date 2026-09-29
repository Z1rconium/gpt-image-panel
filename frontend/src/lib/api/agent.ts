import { apiFetch } from './client';
import { openJsonEventSource } from './events';
import type {
  AgentConversationDetail,
  AgentConversationListResponse,
  AgentConversationSummary,
  AgentStreamEvent,
  AgentStreamEventName,
  AgentTurnAccepted,
  AgentTurnRequest,
  AgentTurnStatusResponse
} from './types/agent';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const AGENT_EVENT_NAMES: AgentStreamEventName[] = [
  'turn.started',
  'block.upsert',
  'block.text',
  'turn.completed',
  'turn.failed',
  'turn.cancelled'
];

export function listAgentConversations(signal?: AbortSignal) {
  return apiFetch<AgentConversationListResponse>('/api/agent/conversations', { signal }, 'loading conversations');
}

export function createAgentConversation(title = '') {
  return apiFetch<AgentConversationSummary>(
    '/api/agent/conversations',
    { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ title }) },
    'creating a conversation'
  );
}

export function getAgentConversation(conversationId: string, signal?: AbortSignal, beforeSeq?: number) {
  const query = beforeSeq ? `?before_seq=${Math.floor(beforeSeq)}` : '';
  return apiFetch<AgentConversationDetail>(
    `/api/agent/conversations/${encodeURIComponent(conversationId)}${query}`,
    { signal },
    'loading the conversation'
  );
}

export function renameAgentConversation(conversationId: string, title: string) {
  return apiFetch<AgentConversationSummary>(
    `/api/agent/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ title }) },
    'renaming the conversation'
  );
}

export function deleteAgentConversation(conversationId: string) {
  return apiFetch<{ status: string; message: string }>(
    `/api/agent/conversations/${encodeURIComponent(conversationId)}`,
    // The server waits for an active turn to stop before deleting, so allow more than the default.
    { method: 'DELETE', signal: AbortSignal.timeout(20_000) },
    'deleting the conversation'
  );
}

export function startAgentTurn(conversationId: string, body: AgentTurnRequest) {
  return apiFetch<AgentTurnAccepted>(
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/turns`,
    { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) },
    'sending the message'
  );
}

export function cancelAgentTurn(turnId: string) {
  return apiFetch<AgentTurnStatusResponse>(
    `/api/agent/turns/${encodeURIComponent(turnId)}/cancel`,
    { method: 'POST' },
    'stopping the reply'
  );
}

export function getAgentTurn(turnId: string) {
  return apiFetch<AgentTurnStatusResponse>(`/api/agent/turns/${encodeURIComponent(turnId)}`, {}, 'checking the reply');
}

export type AgentTurnEventHandlers = {
  onEvent: (event: AgentStreamEvent, lastEventId: number) => void;
  onError?: (error?: unknown) => void;
  onNetworkError?: (event: Event) => void;
};

/**
 * Follow one turn. The server replays from `after` and then tails live events;
 * EventSource reconnects on its own and resumes through Last-Event-ID.
 */
export function openAgentTurnEvents(turnId: string, after: number, handlers: AgentTurnEventHandlers): EventSource {
  const url = `/api/agent/turns/${encodeURIComponent(turnId)}/events?after=${Math.max(0, Math.floor(after))}`;
  return openJsonEventSource<AgentStreamEvent['data']>(
    url,
    {
      onEvent: ({ event, data, lastEventId }) =>
        handlers.onEvent({ event, data } as AgentStreamEvent, lastEventId),
      onError: handlers.onError,
      onNetworkError: handlers.onNetworkError
    },
    AGENT_EVENT_NAMES
  );
}
