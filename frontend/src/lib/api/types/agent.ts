export type AgentTurnStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export type AgentMessageStatus = 'streaming' | 'complete' | 'failed' | 'cancelled' | 'interrupted';
export type AgentImageStatus = 'pending' | 'succeeded' | 'failed' | 'cancelled';

export type AgentTextBlock = {
  id: string;
  type: 'text';
  text: string;
};

export type AgentBatchParamsBlock = {
  id: string;
  type: 'batch_params';
  call_id: string;
  status: 'streaming' | 'ready' | 'invalid';
  items: { id: string; prompt: string }[];
};

export type AgentImageTaskBlock = {
  id: string;
  type: 'image_task';
  call_id: string;
  item_id: string;
  ref_label: string;
  round_no: number;
  path_round_no?: number;
  image_index: number;
  job_id: string | null;
  prompt: string;
  mode: 'generate' | 'edit';
  source_refs: string[];
  status: AgentImageStatus;
  stage: string;
  error: string | null;
  image_id: string | null;
  filename: string | null;
  deleted?: boolean;
};

export type AgentErrorBlock = {
  id: string;
  type: 'error';
  message: string;
};

export type AgentSearchBlock = {
  id: string; type: 'search'; call_id: string; status: string; action: string; queries: string[]; url: string;
};
export type AgentSource = {
  id: string; title: string; url: string; text_block_id: string | null;
  start_index: number | null; end_index: number | null; excerpt: string;
};
export type AgentSourcesBlock = { id: string; type: 'sources'; sources: AgentSource[] };
export type AgentBlock = AgentTextBlock | AgentBatchParamsBlock | AgentImageTaskBlock | AgentErrorBlock | AgentSearchBlock | AgentSourcesBlock;

export type AgentConversationSummary = {
  id: string;
  title: string;
  message_count: number;
  turn_count: number;
  created_at: string;
  updated_at: string;
  active_turn_id: string | null;
  selected_turn_id?: string | null;
  branch_revision?: number;
};

export type AgentConversationListResponse = {
  items: AgentConversationSummary[];
};

export type AgentImageRef = {
  image_ref_id?: string;
  turn_id?: string;
  ref_label: string;
  round_no: number;
  path_round_no?: number;
  image_index: number;
  role: 'input' | 'output';
  image_id: string | null;
  filename: string | null;
  job_id: string | null;
  item_id: string | null;
  prompt: string;
  mode: string;
  status: AgentImageStatus;
  error: string | null;
  deleted: boolean;
  message_id: string;
};

export type AgentMessage = {
  id: string;
  turn_id: string;
  seq: number;
  round_no: number;
  path_round_no?: number;
  role: 'user' | 'assistant';
  text: string;
  blocks: AgentBlock[];
  status: AgentMessageStatus;
  created_at: string;
  updated_at: string;
};

export type AgentActiveTurn = {
  id: string;
  status: AgentTurnStatus;
  round_no: number;
  path_round_no?: number;
};

export type AgentConversationDetail = {
  conversation: AgentConversationSummary;
  messages: AgentMessage[];
  image_refs: AgentImageRef[];
  active_turn: AgentActiveTurn | null;
  has_more: boolean;
  branches?: AgentBranch[];
};

export type AgentImageParams = {
  size: string;
  quality: 'auto' | 'low' | 'medium' | 'high';
  output_format: 'png' | 'jpeg' | 'webp';
};

export type AgentTurnRequest = {
  client_turn_id: string;
  action?: 'continue' | 'edit' | 'regenerate';
  source_turn_id?: string;
  branch_revision?: number;
  text: string;
  attachments: { kind: 'gallery'; image_id: string }[];
  image_params: AgentImageParams;
};

export type AgentTurnAccepted = {
  turn_id: string;
  conversation_id: string;
  round_no: number;
  path_round_no?: number;
  status: AgentTurnStatus;
  user_message_id: string;
  assistant_message_id: string;
  replayed: boolean;
};

export type AgentTurnStatusResponse = {
  turn_id: string;
  conversation_id: string;
  round_no: number;
  path_round_no?: number;
  status: AgentTurnStatus;
  rounds_used: number;
  error_message: string | null;
};

export type AgentStreamEvent =
  | { event: 'turn.started'; data: { turn_id: string; round_no: number; model: string } }
  | { event: 'block.upsert'; data: { block: AgentBlock } }
  | { event: 'block.text'; data: { block_id: string; delta: string } }
  | { event: 'turn.completed'; data: { rounds_used: number } }
  | { event: 'turn.failed'; data: { message: string } }
  | { event: 'turn.cancelled'; data: Record<string, never> };

export type AgentStreamEventName = AgentStreamEvent['event'];

export type AgentBranch = { id: string; parent_turn_id: string | null; round_no: number; path_round_no: number; preview: string; status: AgentTurnStatus };
