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

// A grounded reply's provenance snapshot (issue #59, ADR-8.6) — captured
// at write time, so it survives intact even after its source chunk is
// later deleted (chunk_id becomes null: the UI's "source removed" state,
// not a broken citation).
export interface Citation {
  id: string;
  chunk_id: string | null;
  document_title: string;
  section_path: string;
  page_start: number | null;
  page_end: number | null;
}

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
  grounded: boolean;
  citations: Citation[];
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

export interface CitationEventData {
  citation_id: string;
  chunk_id: string | null;
  document_title: string;
  section_path: string;
  page_start: number | null;
  page_end: number | null;
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

// Usage + budget (issue #39, §3.2.14) — mirrors http/schemas/metering.py.
export interface UsageModelRollup {
  model: string;
  request_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_microcents: number;
}

export interface UsageResponse {
  workspace_id: string;
  period_start: string;
  by_model: UsageModelRollup[];
  total_cost_microcents: number;
}

export interface BudgetResponse {
  workspace_id: string;
  monthly_limit_microcents: number;
  soft_pct: number;
  current_period_start: string;
  settled_microcents: number;
  updated_at: string;
}

// Documents (issue #48, §4.3, FR-KB-2/5) — mirrors http/schemas/documents.py.
export type DocumentStatus =
  "queued" | "scanning" | "parsing" | "chunking" | "embedding" | "ready" | "failed";

export interface DocumentRecord {
  id: string;
  workspace_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  status: DocumentStatus;
  failure_stage: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  items: DocumentRecord[];
  next_cursor: string | null;
}

export interface InitiateUploadResponse {
  document_id: string;
  object_key: string;
  upload_url: string;
  upload_fields: Record<string, string>;
}

export const TERMINAL_DOCUMENT_STATUSES: ReadonlySet<DocumentStatus> = new Set(["ready", "failed"]);
