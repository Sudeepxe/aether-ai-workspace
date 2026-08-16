/**
 * Drives one chat turn's full stream lifecycle (§5.2): composing ->
 * submitted -> streaming -> settled | partial | cancelled | errored.
 * Every path lands in a defined terminal state — no "spinner forever".
 * On settle, the finished message is invalidated into TanStack Query's
 * cache (the canonical store) and the Zustand stream buffer is cleared —
 * server state stays canonical, streaming stays fast (ADR-5.2).
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { cancelGeneration, postMessageForStream } from "../api/chat";
import type { DoneEventData, ErrorEventData, MetaEventData, TokenEventData } from "../api/types";
import { toApiError } from "../lib/apiClient";
import { SeqDeduper, streamSse } from "../lib/sse";
import { RafBatcher, useChatStreamStore, type StreamPhase } from "../state/chatStreamStore";

export function useSendMessage(
  workspaceId: string,
  threadId: string,
): {
  send: (content: string) => Promise<void>;
  cancel: () => void;
} {
  const queryClient = useQueryClient();
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (content: string) => {
      const clientMessageId = crypto.randomUUID();
      const controller = new AbortController();
      abortRef.current = controller;

      let response: Response;
      try {
        response = await postMessageForStream(
          workspaceId,
          threadId,
          { content, client_message_id: clientMessageId },
          controller.signal,
        );
      } catch {
        useChatStreamStore.getState().failBeforeStream(threadId, "network error sending message");
        return;
      }
      if (!response.ok) {
        const apiError = await toApiError(response);
        // Refused before any provider call (§3.2.14) — a distinct,
        // actionable message rather than a bare status code, since this
        // is the one failure mode a user can actually do something
        // about (wait for the next billing period, or an Admin raises
        // the workspace's budget via PUT .../budget).
        useChatStreamStore
          .getState()
          .failBeforeStream(
            threadId,
            apiError.code === "budget_exhausted"
              ? "This workspace's monthly budget has been used up. An admin can raise it, or wait for the next billing period."
              : apiError.message,
          );
        return;
      }

      const dedupe = new SeqDeduper();
      const batcher = new RafBatcher((accumulated) =>
        useChatStreamStore.getState().flushContent(accumulated),
      );

      for await (const frame of streamSse(response, controller.signal)) {
        if (!dedupe.admit(frame)) {
          continue;
        }
        switch (frame.event) {
          case "meta": {
            const data = frame.data as MetaEventData;
            useChatStreamStore.getState().begin(threadId, data.generation_id);
            useChatStreamStore.getState().setPhase("streaming");
            break;
          }
          case "token": {
            batcher.append((frame.data as TokenEventData).delta);
            break;
          }
          case "done": {
            batcher.flushNow();
            const data = frame.data as DoneEventData;
            // §4.4's done-event vocabulary (complete|partial|cancelled|error)
            // and §5.2's client stream-phase vocabulary
            // (settled|partial|cancelled|errored) name the same terminal
            // states differently — "settled" vs "complete" — so this is
            // the one deliberate translation point between them.
            const phase: StreamPhase =
              data.status === "error"
                ? "errored"
                : data.status === "complete"
                  ? "settled"
                  : data.status;
            useChatStreamStore.getState().setPhase(phase);
            await queryClient.invalidateQueries({ queryKey: ["messages", workspaceId, threadId] });
            // Settlement (real cost) lands async-to-the-stream but
            // same-request (see the orchestrator's send_message.py) —
            // by the time `done` arrives it's already written, so the
            // budget indicator can refresh right away instead of
            // waiting on its 30s poll.
            await queryClient.invalidateQueries({ queryKey: ["budget", workspaceId] });
            useChatStreamStore.getState().clear();
            break;
          }
          case "error": {
            const data = frame.data as ErrorEventData;
            useChatStreamStore.getState().setError(data.message);
            break;
          }
          default:
            break;
        }
      }
    },
    [workspaceId, threadId, queryClient],
  );

  const cancel = useCallback(() => {
    const generationId = useChatStreamStore.getState().activeStream?.generationId;
    abortRef.current?.abort();
    if (generationId !== undefined && generationId !== null) {
      // Fire-and-forget: aborting the client read alone doesn't free
      // server-side generation capacity (ADR-5.3) — this call does.
      void cancelGeneration(workspaceId, generationId);
    }
  }, [workspaceId]);

  return { send, cancel };
}
