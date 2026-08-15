import { describe, expect, it } from "vitest";

import { parseSseBlock, SeqDeduper, splitSseBlocks, streamSse } from "./sse";

describe("parseSseBlock", () => {
  it("parses id/event/data lines into a frame", () => {
    const frame = parseSseBlock('id: gen-1:0\nevent: meta\ndata: {"model":"echo-v1"}');
    expect(frame).toEqual({ id: "gen-1:0", event: "meta", data: { model: "echo-v1" } });
  });

  it("defaults event to 'message' when absent", () => {
    const frame = parseSseBlock('id: gen-1:1\ndata: {"delta":"hi"}');
    expect(frame?.event).toBe("message");
  });

  it("falls back to the raw string when data isn't JSON", () => {
    const frame = parseSseBlock("id: gen-1:2\nevent: token\ndata: not-json{");
    expect(frame?.data).toBe("not-json{");
  });

  it("returns null for a heartbeat comment frame", () => {
    expect(parseSseBlock(": heartbeat")).toBeNull();
  });

  it("returns null for an empty block", () => {
    expect(parseSseBlock("")).toBeNull();
    expect(parseSseBlock("   ")).toBeNull();
  });

  it("returns null for a block with no id and no data", () => {
    expect(parseSseBlock("event: token")).toBeNull();
  });
});

describe("splitSseBlocks", () => {
  it("splits complete blocks on the double-newline delimiter", () => {
    const { blocks, rest } = splitSseBlocks("id: 1\ndata: a\n\nid: 2\ndata: b\n\n");
    expect(blocks).toEqual(["id: 1\ndata: a", "id: 2\ndata: b"]);
    expect(rest).toBe("");
  });

  it("keeps a partial trailing block in rest, unsplit", () => {
    // A real network read can split a frame anywhere, including mid-line —
    // the buffer/reader loop must accumulate until the delimiter arrives.
    const { blocks, rest } = splitSseBlocks("id: 1\ndata: a\n\nid: 2\ndata: b");
    expect(blocks).toEqual(["id: 1\ndata: a"]);
    expect(rest).toBe("id: 2\ndata: b");
  });

  it("returns no blocks and the whole buffer as rest when no delimiter is present yet", () => {
    const { blocks, rest } = splitSseBlocks("id: 1\ndata: partial-chunk");
    expect(blocks).toEqual([]);
    expect(rest).toBe("id: 1\ndata: partial-chunk");
  });
});

describe("SeqDeduper", () => {
  it("admits strictly increasing seqs for the same generation", () => {
    const dedupe = new SeqDeduper();
    expect(dedupe.admit({ id: "gen-1:0", event: "meta", data: null })).toBe(true);
    expect(dedupe.admit({ id: "gen-1:1", event: "token", data: null })).toBe(true);
    expect(dedupe.admit({ id: "gen-1:2", event: "token", data: null })).toBe(true);
  });

  it("discards a redelivered (duplicate or earlier) seq — the exact resume property §4.4 requires", () => {
    const dedupe = new SeqDeduper();
    dedupe.admit({ id: "gen-1:0", event: "meta", data: null });
    dedupe.admit({ id: "gen-1:1", event: "token", data: null });
    expect(dedupe.admit({ id: "gen-1:1", event: "token", data: null })).toBe(false);
    expect(dedupe.admit({ id: "gen-1:0", event: "meta", data: null })).toBe(false);
  });

  it("admits a frame with no id (defensive default, never actually emitted by the server)", () => {
    const dedupe = new SeqDeduper();
    expect(dedupe.admit({ id: null, event: "message", data: null })).toBe(true);
  });
});

function sseResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream);
}

describe("streamSse", () => {
  it("yields frames as complete blocks arrive, even split mid-frame across chunks", async () => {
    const response = sseResponse([
      "id: gen-1:0\nevent: meta\ndata: {}\n\nid: gen-1:1\nev",
      'ent: token\ndata: {"delta":"hi"}\n\n',
    ]);
    const controller = new AbortController();
    const frames = [];
    for await (const frame of streamSse(response, controller.signal)) {
      frames.push(frame);
    }
    expect(frames).toEqual([
      { id: "gen-1:0", event: "meta", data: {} },
      { id: "gen-1:1", event: "token", data: { delta: "hi" } },
    ]);
  });

  it("stops yielding once the signal is aborted", async () => {
    const response = sseResponse(["id: gen-1:0\nevent: meta\ndata: {}\n\n"]);
    const controller = new AbortController();
    controller.abort();
    const frames = [];
    for await (const frame of streamSse(response, controller.signal)) {
      frames.push(frame);
    }
    expect(frames).toEqual([]);
  });
});
