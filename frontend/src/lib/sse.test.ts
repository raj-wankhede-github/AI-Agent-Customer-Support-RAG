import { describe, expect, it } from "vitest";
import { createSSEParser, type SSEEvent } from "./sse";

describe("createSSEParser", () => {
  it("reassembles events split across arbitrary chunk boundaries", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    const stream = 'event: status\ndata: {"stage":"processing"}\n\n: keepalive\n\nevent: delta\ndata: {"text":"Hello "}\n\nevent: done\ndata: {"ok":true}\n\n';
    for (let i = 0; i < stream.length; i += 7) parser.push(stream.slice(i, i + 7));
    parser.flush();
    expect(events).toEqual([
      { event: "status", data: '{"stage":"processing"}' },
      { event: "delta", data: '{"text":"Hello "}' },
      { event: "done", data: '{"ok":true}' },
    ]);
  });

  it("handles CRLF line endings and multi-line data", () => {
    const events: SSEEvent[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push("data: line one\r\ndata: line two\r\n\r\n");
    expect(events).toEqual([{ event: "message", data: "line one\nline two" }]);
  });
});
