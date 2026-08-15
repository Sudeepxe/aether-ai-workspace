export interface Workspace {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  updated_at: string;
}

export interface Thread {
  id: string;
  workspace_id: string;
  created_by: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus = "complete" | "partial" | "cancelled";

export interface ChatMessage {
  id: string;
  workspace_id: string;
  thread_id: string;
  seq: number;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  client_message_id: string | null;
  model: string | null;
  created_at: string;
}

export interface MessageListResponse {
  items: ChatMessage[];
  next_cursor: string | null;
}

// SSE event payloads (§4.4) — the shapes domain/streaming.py's
// event_payload() serializes, mirrored here for the client parser.
export interface MetaEventData {
  generation_id: string;
  model: string;
  grounded: boolean;
}

export interface TokenEventData {
  delta: string;
}

export interface UsageEventData {
  prompt_tokens: number;
  completion_tokens: number;
  cost_microcents: number;
}

export type DoneStatus = "complete" | "partial" | "cancelled" | "error";

export interface DoneEventData {
  status: DoneStatus;
}

export interface ErrorEventData {
  code: string;
  message: string;
}
