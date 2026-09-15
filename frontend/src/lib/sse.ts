export interface SSEEvent {
  event: string;
  data: string;
}

/**
 * Incremental Server-Sent Events parser. Feed it decoded text chunks in any split; it emits
 * complete events. Comment lines (keepalives) are ignored.
 */
export function createSSEParser(onEvent: (event: SSEEvent) => void) {
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];

  function dispatch() {
    if (dataLines.length > 0) onEvent({ event: eventName, data: dataLines.join("\n") });
    eventName = "message";
    dataLines = [];
  }

  return {
    push(chunk: string) {
      buffer += chunk.replace(/\r\n?/g, "\n");
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (line === "") dispatch();
        else if (line.startsWith(":")) {
          // keepalive comment
        } else {
          const colon = line.indexOf(":");
          const field = colon === -1 ? line : line.slice(0, colon);
          const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
          if (field === "event") eventName = value;
          else if (field === "data") dataLines.push(value);
        }
        newline = buffer.indexOf("\n");
      }
    },
    flush() {
      if (buffer.trim()) this.push("\n");
      dispatch();
    },
  };
}
