import { apiFetch, apiFetchRaw } from "../lib/apiClient";
import type { MessageListResponse, Thread, Workspace } from "./types";

export function createWorkspace(name: string): Promise<Workspace> {
  return apiFetch<Workspace>("/v1/workspaces", { method: "POST", body: { name } });
}

export function createThread(workspaceId: string, title?: string): Promise<Thread> {
  return apiFetch<Thread>(`/v1/workspaces/${workspaceId}/threads`, {
    method: "POST",
    body: { title: title ?? null },
  });
}

export function listMessages(workspaceId: string, threadId: string): Promise<MessageListResponse> {
  return apiFetch<MessageListResponse>(
    `/v1/workspaces/${workspaceId}/threads/${threadId}/messages`,
  );
}

/** Returns the raw streaming Response — callers drive it with
 * lib/sse.ts's streamSse(), since a StreamingResponse body can't go
 * through apiFetch's JSON parsing. */
export function postMessageForStream(
  workspaceId: string,
  threadId: string,
  body: { content: string; client_message_id: string },
  signal: AbortSignal,
): Promise<Response> {
  return apiFetchRaw(`/v1/workspaces/${workspaceId}/threads/${threadId}/messages`, {
    method: "POST",
    body,
    headers: { Accept: "text/event-stream" },
    signal,
  });
}

export function cancelGeneration(workspaceId: string, generationId: string): Promise<void> {
  return apiFetch<void>(`/v1/workspaces/${workspaceId}/generations/${generationId}`, {
    method: "DELETE",
  });
}
