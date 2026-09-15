import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, errorMessage, request, SESSION_EXPIRED_EVENT, streamMessage } from "./api";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request", () => {
  it("sends the CSRF header and parses JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("POST", "/api/conversations", { body: {} })).resolves.toEqual({ ok: true });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Requested-With"]).toBe("XMLHttpRequest");
    expect(init.credentials).toBe("same-origin");
  });

  it("maps backend errors to ApiError and never exposes raw bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(429, { error: { code: "RATE_LIMITED", message: "Too many requests.", request_id: "abc123" } }, { "Retry-After": "12" }),
    ));
    const error = await request("GET", "/api/conversations").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "RATE_LIMITED", requestId: "abc123", retryAfter: 12 });
    expect(errorMessage(error)).toContain("wait 12 seconds");
  });

  it("announces session expiry on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { error: { code: "AUTHENTICATION_ERROR", message: "Your session has expired." } })));
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    await request("GET", "/api/conversations").catch(() => undefined);
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("turns network failures into a friendly error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await request("GET", "/api/auth/me").catch((e) => e);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: 0 });
    expect(errorMessage(error)).toMatch(/can't reach the support service/);
  });
});

describe("streamMessage", () => {
  it("delivers deltas and resolves with the final validated response", async () => {
    const final = { conversation_id: "c1", answer: "Standard shipping takes 3-5 business days.", answer_status: "ANSWERED" };
    const body = [
      'event: status\ndata: {"stage":"processing"}\n\n',
      'event: delta\ndata: {"text":"Standard shipping "}\n\n',
      'event: delta\ndata: {"text":"takes 3-5 business days."}\n\n',
      `event: done\ndata: ${JSON.stringify(final)}\n\n`,
    ].join("");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    const deltas: string[] = [];
    const result = await streamMessage("c1", "hi", "id-1", { onDelta: (t) => deltas.push(t) });
    expect(deltas.join("")).toBe(final.answer);
    expect(result).toMatchObject(final);
  });

  it("rejects when the stream reports an error", async () => {
    const body = 'event: error\ndata: {"code":"CONFLICT","message":"This conversation is closed.","status":409}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    await expect(streamMessage("c1", "hi", "id-2", {})).rejects.toMatchObject({ code: "CONFLICT", status: 409 });
  });
});
