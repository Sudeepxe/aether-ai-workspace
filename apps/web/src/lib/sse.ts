/**
 * Hand-rolled SSE frame parser over fetch()+ReadableStream (ADR-5.3:
 * native EventSource is GET-only and can't carry the Authorization
 * header or POST body this contract needs). Pure parsing logic, kept
 * separate from the fetch/AbortController plumbing so it's unit-testable
 * without a real network stack — this is called out in the blueprint as
 * the highest-defect-density code in any streaming UI, so it gets
 * exhaustive tests, not just usage.
 */

export interface SseFrame {
  /** "{generation_id}:{seq}", or null for a frame with no id line. */
  id: string | null;
  event: string;
  data: unknown;
}

/** Parses one already-split "id:/event:/data:" block (no trailing blank
 * line). Returns null for heartbeat comment frames (leading ":") and
 * for a block with no recognizable field at all. */
export function parseSseBlock(block: string): SseFrame | null {
  if (block.startsWith(":") || block.trim() === "") {
    return null;
  }
  let id: string | null = null;
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("id: ")) {
      id = line.slice(4);
    } else if (line.startsWith("event: ")) {
      event = line.slice(7);
    } else if (line.startsWith("data: ")) {
      dataLines.push(line.slice(6));
    }
  }
  if (id === null && dataLines.length === 0) {
    return null;
  }
  const raw = dataLines.join("\n");
  let data: unknown = null;
  if (raw.length > 0) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
  }
  return { id, event, data };
}

/** Splits a growing text buffer on the SSE frame delimiter ("\n\n"),
 * returning the complete blocks found and the leftover partial tail to
 * keep accumulating — the buffer/reader loop lives in streamSse() below;
 * this half is pure and independently testable against partial chunks
 * arriving mid-frame (a real behavior of network reads, not a
 * hypothetical). */
export function splitSseBlocks(buffer: string): { blocks: string[]; rest: string } {
  const blocks: string[] = [];
  let rest = buffer;
  let idx = rest.indexOf("\n\n");
  while (idx !== -1) {
    blocks.push(rest.slice(0, idx));
    rest = rest.slice(idx + 2);
    idx = rest.indexOf("\n\n");
  }
  return { blocks, rest };
}

/** Reads an SSE response body incrementally, yielding parsed frames as
 * they arrive. Stops when the stream ends or ``signal`` aborts —
 * aborting here only stops *reading*; the caller is responsible for
 * also telling the server to stop generating (DELETE /generations/{id},
 * ADR-5.3: closing the client stream alone doesn't free provider
 * capacity). */
export async function* streamSse(
  response: Response,
  signal: AbortSignal,
): AsyncGenerator<SseFrame> {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("response has no readable body");
  }
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) {
        return;
      }
      buffer += decoder.decode(value, { stream: true });
      const { blocks, rest } = splitSseBlocks(buffer);
      buffer = rest;
      for (const block of blocks) {
        const frame = parseSseBlock(block);
        if (frame !== null) {
          yield frame;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** seq is strictly monotonic per generation (§4.4); a resumed stream can
 * redeliver at-least-once, so the client discards duplicates by seq —
 * exactly-once rendering on top of at-least-once delivery. Stateful
 * across the whole generation's lifetime, so it lives as a tiny class
 * rather than a pure function. */
export class SeqDeduper {
  private lastSeq = -1;

  admit(frame: SseFrame): boolean {
    if (frame.id === null) {
      return true;
    }
    const seq = Number(frame.id.slice(frame.id.lastIndexOf(":") + 1));
    if (Number.isNaN(seq) || seq <= this.lastSeq) {
      return false;
    }
    this.lastSeq = seq;
    return true;
  }
}
