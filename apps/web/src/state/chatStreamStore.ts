/**
 * The one deliberate exception to "server state lives in TanStack Query"
 * (ADR-5.2): an in-flight generation's tokens append to this Zustand
 * buffer, flushed in rAF batches (~60fps ceiling) so a fast stream
 * doesn't cause one React re-render per token. On settle, the caller
 * writes the finished message into the TanStack Query cache and clears
 * this buffer — this store never holds the settled, canonical content.
 */
import { create } from "zustand";

export type StreamPhase =
  "submitted" | "streaming" | "settled" | "partial" | "cancelled" | "errored";

interface ActiveStream {
  generationId: string;
  threadId: string;
  phase: StreamPhase;
  content: string;
  errorMessage: string | null;
}

interface ChatStreamState {
  activeStream: ActiveStream | null;
  begin: (threadId: string, generationId: string) => void;
  setPhase: (phase: StreamPhase) => void;
  flushContent: (content: string) => void;
  setError: (message: string) => void;
  clear: () => void;
}

export const useChatStreamStore = create<ChatStreamState>((set) => ({
  activeStream: null,
  begin: (threadId, generationId) =>
    set({
      activeStream: {
        threadId,
        generationId,
        phase: "submitted",
        content: "",
        errorMessage: null,
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
  setError: (message) =>
    set((state) =>
      state.activeStream
        ? { activeStream: { ...state.activeStream, phase: "errored", errorMessage: message } }
        : state,
    ),
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
