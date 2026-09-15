import { createSSEParser } from "./sse";
import type {
  AdminConversation,
  AdminConversationDetail,
  ChatResponse,
  Conversation,
  ConversationDetail,
  ConversationStatus,
  DocumentDetail,
  DocumentStatus,
  FeedbackRating,
  FeedbackReason,
  Handoff,
  HandoffStatus,
  KnowledgeDocument,
  Message,
  Metrics,
  Page,
  RetrievalDebug,
  UploadResponse,
  User,
  UserRef,
  Version,
} from "./types";

export const SESSION_EXPIRED_EVENT = "support:session-expired";
const DEFAULT_TIMEOUT_MS = 20_000;
const CHAT_TIMEOUT_MS = 150_000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, requestId: string | null = null, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}

/** Customer-safe text for any error. Backend messages are already safe; network cases are mapped here. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "RATE_LIMITED") {
      return error.retryAfter
        ? `You're sending requests too quickly. Please wait ${error.retryAfter} seconds and try again.`
        : error.message;
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

interface RequestOptions {
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  timeoutMs?: number;
  query?: Record<string, string | number | undefined | null>;
}

function buildUrl(path: string, query?: RequestOptions["query"]) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

function withTimeout(signal: AbortSignal | undefined, timeoutMs: number): AbortSignal {
  if (typeof AbortSignal.timeout === "function" && typeof AbortSignal.any === "function") {
    const timeout = AbortSignal.timeout(timeoutMs);
    return signal ? AbortSignal.any([signal, timeout]) : timeout;
  }
  // Fallback for runtimes without AbortSignal.timeout/any.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), timeoutMs);
  signal?.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  controller.signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  return controller.signal;
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = "INTERNAL_ERROR";
  let message = "Something went wrong. Please try again.";
  let requestId: string | null = response.headers.get("x-request-id");
  try {
    const body = await response.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      requestId = body.error.request_id ?? requestId;
    }
  } catch {
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      code = "SERVICE_UNAVAILABLE";
      message = "The support service is temporarily unavailable. Please try again shortly.";
    }
  }
  const retryAfter = Number(response.headers.get("retry-after")) || null;
  return new ApiError(response.status, code, message, requestId, retryAfter);
}

function networkError(error: unknown): ApiError {
  if (error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError")) {
    return new ApiError(0, error.name === "TimeoutError" ? "TIMEOUT" : "ABORTED", "The request took too long. Please try again.");
  }
  return new ApiError(0, "NETWORK_ERROR", "We can't reach the support service. Check your connection and try again.");
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: withTimeout(options.signal, options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    throw networkError(error);
  }
  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/me") {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface StreamHandlers {
  onStatus?: (stage: string) => void;
  onDelta?: (text: string) => void;
}

/** POST a chat message and consume the validated SSE response. Resolves with the final ChatResponse. */
export async function streamMessage(
  conversationId: string,
  content: string,
  clientMessageId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(`/api/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ content, client_message_id: clientMessageId }),
      credentials: "same-origin",
      signal: withTimeout(signal, CHAT_TIMEOUT_MS),
    });
  } catch (error) {
    throw networkError(error);
  }
  if (!response.ok || !response.body) {
    const error = await toApiError(response);
    if (response.status === 401) window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
    throw error;
  }

  let result: ChatResponse | null = null;
  let failure: ApiError | null = null;
  const parser = createSSEParser(({ event, data }) => {
    const payload = JSON.parse(data);
    if (event === "status") handlers.onStatus?.(payload.stage);
    else if (event === "delta") handlers.onDelta?.(payload.text);
    else if (event === "done") result = payload as ChatResponse;
    else if (event === "error") failure = new ApiError(payload.status ?? 500, payload.code, payload.message);
  });
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(value);
    }
    parser.flush();
  } catch (error) {
    throw networkError(error);
  }
  if (failure) throw failure;
  if (!result) throw new ApiError(0, "STREAM_INTERRUPTED", "The connection was interrupted before the response finished.");
  return result;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ user: User }>("POST", "/api/auth/login", { body: { email, password } }).then((r) => r.user),
  logout: () => request<void>("POST", "/api/auth/logout"),
  me: () => request<User>("GET", "/api/auth/me"),

  listConversations: (params: { page?: number; q?: string; status?: ConversationStatus }) =>
    request<Page<Conversation>>("GET", "/api/conversations", { query: { page_size: 20, ...params } }),
  /** Returns an existing open conversation that started with the same question instead of a duplicate. */
  createConversation: (firstMessage?: string) =>
    request<Conversation & { reused: boolean }>("POST", "/api/conversations", { body: { first_message: firstMessage ?? null } }),
  getConversation: (id: string, signal?: AbortSignal) => request<ConversationDetail>("GET", `/api/conversations/${id}`, { signal }),
  requestHandoff: (id: string) => request<ChatResponse>("POST", `/api/conversations/${id}/handoff`, { body: {} }),
  sendFeedback: (id: string, body: { message_id: string; rating: FeedbackRating; reason?: FeedbackReason | null; comment?: string | null }) =>
    request<void>("POST", `/api/conversations/${id}/feedback`, { body }),
  closeConversation: (id: string) => request<Conversation>("POST", `/api/conversations/${id}/close`, { body: {} }),
  deleteConversation: (id: string) => request<void>("DELETE", `/api/conversations/${id}`),

  metrics: (days: number) => request<Metrics>("GET", "/api/admin/metrics", { query: { days } }),
  handoffs: (params: { page?: number; status?: HandoffStatus }) =>
    request<Page<Handoff>>("GET", "/api/admin/handoffs", { query: { page_size: 20, ...params } }),
  assignHandoff: (id: string, agentId?: string) =>
    request<Handoff>("POST", `/api/admin/handoffs/${id}/assign`, { body: { agent_id: agentId ?? null } }),
  adminConversations: (params: { page?: number; q?: string; status?: ConversationStatus; handoff_status?: HandoffStatus }) =>
    request<Page<AdminConversation>>("GET", "/api/admin/conversations", { query: { page_size: 20, ...params } }),
  adminConversation: (id: string) => request<AdminConversationDetail>("GET", `/api/admin/conversations/${id}`),
  agentReply: (id: string, content: string) => request<Message>("POST", `/api/admin/conversations/${id}/messages`, { body: { content } }),
  resolveConversation: (id: string) => request<AdminConversation>("POST", `/api/admin/conversations/${id}/resolve`, { body: {} }),
  adminCloseConversation: (id: string) => request<AdminConversation>("POST", `/api/admin/conversations/${id}/close`, { body: {} }),
  agents: () => request<UserRef[]>("GET", "/api/admin/agents"),
  retrievalDebug: (query: string) => request<RetrievalDebug>("POST", "/api/admin/retrieval/debug", { body: { query }, timeoutMs: 60_000 }),

  documents: (params: { page?: number; q?: string; status?: DocumentStatus }) =>
    request<Page<KnowledgeDocument>>("GET", "/api/knowledge/documents", { query: { page_size: 20, ...params } }),
  document: (id: string) => request<DocumentDetail>("GET", `/api/knowledge/documents/${id}`),
  uploadDocument: (form: FormData) =>
    request<UploadResponse>("POST", "/api/knowledge/documents", { form, timeoutMs: 120_000 }),
  updateDocument: (id: string, body: Partial<Pick<KnowledgeDocument, "title" | "authority" | "category" | "product" | "locale" | "effective_date" | "source_uri" | "status">>) =>
    request<KnowledgeDocument>("PATCH", `/api/knowledge/documents/${id}`, { body }),
  deleteDocument: (id: string) => request<void>("DELETE", `/api/knowledge/documents/${id}`),
  reindexDocument: (id: string) => request<Version>("POST", `/api/knowledge/documents/${id}/reindex`, { body: {} }),
};
