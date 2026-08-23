/**
 * The one deliberate exception to "server state lives in TanStack Query"
 * (ADR-5.2): an in-flight generation's tokens append to this Zustand
 * buffer, flushed in rAF batches (~60fps ceiling) so a fast stream
 * doesn't cause one React re-render per token. On settle, the caller
 * writes the finished message into the TanStack Query cache and clears
 * this buffer — this store never holds the settled, canonical content.
 */
import { create } from "zustand";

import type { Citation } from "../api/types";

export type StreamPhase =
  "submitted" | "streaming" | "settled" | "partial" | "cancelled" | "errored";

interface ActiveStream {
  // null before a generation_id exists yet — a failure before the first
  // SSE `meta` event (network error, a refused request such as
  // budget_exhausted) has no generation to attach to.
  generationId: string | null;
  threadId: string;
  phase: StreamPhase;
  content: string;
  errorMessage: string | null;
  // null until the `meta` event arrives (same lifecycle as generationId) —
  // the real Gate 1 outcome (issue #60), not a guess rendered before the
  // server has actually decided.
  grounded: boolean | null;
  // Filled in by `citation` SSE events as they arrive, so a grounded
  // answer's provenance renders while it's still streaming, not only
  // after settle + a message-list refetch.
  citations: Citation[];
}

interface ChatStreamState {
  activeStream: ActiveStream | null;
  begin: (threadId: string, generationId: string, grounded: boolean) => void;
  setPhase: (phase: StreamPhase) => void;
  flushContent: (content: string) => void;
  addCitation: (citation: Citation) => void;
  setError: (message: string) => void;
  /** For a failure before any stream started (no `begin()` call yet) —
   * unlike setError, this always produces a visible error state instead
   * of silently no-oping when activeStream is still null. */
  failBeforeStream: (threadId: string, message: string) => void;
  clear: () => void;
}

export const useChatStreamStore = create<ChatStreamState>((set) => ({
  activeStream: null,
  begin: (threadId, generationId, grounded) =>
    set({
      activeStream: {
        threadId,
        generationId,
        phase: "submitted",
        content: "",
        errorMessage: null,
        grounded,
        citations: [],
      },
    }),
  setPhase: (phase) =>
    set((state) =>
      state.activeStream ? { activeStream: { ...state.activeStream, phase } } : state,
    ),
  flushContent: (content) =>
    set((state) =>
      state.activeStream ? { activeStream: { ...state.activeStream, content } } : state,
    ),
  addCitation: (citation) =>
    set((state) =>
      state.activeStream
        ? {
            activeStream: {
              ...state.activeStream,
              citations: [...state.activeStream.citations, citation],
            },
          }
        : state,
    ),
  setError: (message) =>
    set((state) =>
      state.activeStream
        ? { activeStream: { ...state.activeStream, phase: "errored", errorMessage: message } }
        : state,
    ),
  failBeforeStream: (threadId, message) =>
    set({
      activeStream: {
        threadId,
        generationId: null,
        phase: "errored",
        content: "",
        errorMessage: message,
        grounded: null,
        citations: [],
      },
    }),
  clear: () => set({ activeStream: null }),
}));

/** Batches rapid appendDelta calls into at most one store write per
 * animation frame — the mechanism, not just the intent, behind "a
 * 100-token/s stream doesn't cause 100 re-renders/s" (ADR-5.2). */
export class RafBatcher {
  private pending = "";
  private scheduled = false;

  constructor(private readonly onFlush: (accumulated: string) => void) {}

  append(delta: string): void {
    this.pending += delta;
    if (!this.scheduled) {
      this.scheduled = true;
      requestAnimationFrame(() => {
        this.scheduled = false;
        this.onFlush(this.pending);
      });
    }
  }

  /** Immediate, unbatched flush — used at stream end so the final
   * content is never left waiting on a frame that may not come. */
  flushNow(): void {
    this.onFlush(this.pending);
  }
}
