import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, streamMessage } from "../../lib/api";
import type { Conversation, FeedbackRating, FeedbackReason, Message } from "../../lib/types";

export type LocalState =
  | { status: "sending"; clientId: string }
  | { status: "failed"; clientId: string; error: string }
  | { status: "streaming"; stage: string };

export interface ChatItem {
  key: string;
  message: Message;
  local?: LocalState;
  sentAt?: number;
}

const POLL_MS = 5000;
const RECONCILE_CODES = new Set(["NETWORK_ERROR", "STREAM_INTERRUPTED", "TIMEOUT"]);

function draftMessage(role: Message["role"], content: string): Message {
  return {
    id: "",
    role,
    content,
    created_at: new Date().toISOString(),
    answer_status: null,
    response_kind: null,
    confidence: null,
    citations: [],
    handoff_reason: null,
    author_name: null,
    feedback: null,
  };
}

export function humanInvolved(conversation: Conversation | null): boolean {
  if (!conversation) return false;
  return (
    conversation.handoff_status === "PENDING" ||
    conversation.handoff_status === "ASSIGNED" ||
    conversation.status === "WAITING_FOR_HUMAN" ||
    conversation.status === "WAITING_FOR_CUSTOMER"
  );
}

/**
 * State for one conversation: server history plus optimistic local items. Only server-validated
 * assistant text is ever shown - streaming starts after the backend finished validation.
 */
export function useConversation(conversationId: string, onChanged?: () => void) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const detail = await api.getConversation(conversationId, signal);
        setConversation(detail.conversation);
        setLoadError(null);
        setItems((previous) => {
          const server: ChatItem[] = detail.messages.map((m) => ({ key: m.id, message: m }));
          // Keep failed local messages unless the server shows they were saved after all.
          const unsent = previous.filter(
            (item) =>
              item.local?.status === "failed" &&
              !detail.messages.some(
                (m) => m.role === "USER" && m.content === item.message.content && new Date(m.created_at).getTime() >= (item.sentAt ?? 0) - 5000,
              ),
          );
          return [...server, ...unsent];
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (error instanceof ApiError && error.code === "ABORTED") return;
        setLoadError(error instanceof ApiError ? error : new ApiError(0, "UNKNOWN", errorMessage(error)));
      } finally {
        setLoading(false);
      }
    },
    [conversationId],
  );

  useEffect(() => {
    setLoading(true);
    setItems([]);
    setConversation(null);
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // While a human is involved, poll so agent replies appear without a refresh.
  const polling = humanInvolved(conversation);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !busy.current) void load();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, load]);

  const send = useCallback(
    async (content: string, retryKey?: string) => {
      const text = content.trim();
      if (!text || busy.current) return;
      busy.current = true;
      setSending(true);
      const existing = retryKey ? items.find((i) => i.key === retryKey) : undefined;
      const clientId = existing?.local && "clientId" in existing.local ? existing.local.clientId : crypto.randomUUID();
      const userKey = retryKey ?? `local-${clientId}`;
      const pendingKey = `pending-${clientId}`;
      const sentAt = Date.now();

      setItems((previous) => [
        ...previous.filter((i) => i.key !== userKey),
        { key: userKey, message: draftMessage("USER", text), local: { status: "sending", clientId }, sentAt },
        { key: pendingKey, message: draftMessage("ASSISTANT", ""), local: { status: "streaming", stage: "processing" } },
      ]);

      try {
        const result = await streamMessage(conversationId, text, clientId, {
          onStatus: (stage) =>
            setItems((previous) => previous.map((i) => (i.key === pendingKey ? { ...i, local: { status: "streaming", stage } } : i))),
          onDelta: (delta) =>
            setItems((previous) =>
              previous.map((i) => (i.key === pendingKey ? { ...i, message: { ...i.message, content: i.message.content + delta } } : i)),
            ),
        });
        setItems((previous) =>
          previous.flatMap((i) => {
            if (i.key === userKey) return [{ key: result.user_message_id, message: { ...i.message, id: result.user_message_id } }];
            if (i.key === pendingKey) return result.message ? [{ key: result.message.id, message: result.message }] : [];
            return [i];
          }),
        );
        setConversation((current) =>
          current
            ? {
                ...current,
                status: result.conversation_status,
                handoff_status:
                  result.answer_status === "HANDOFF_REQUIRED" && (current.handoff_status === "NONE" || current.handoff_status === "RESOLVED")
                    ? "PENDING"
                    : current.handoff_status,
                title: current.title === "New conversation" ? text.slice(0, 60) : current.title,
              }
            : current,
        );
        // Replying to a resolved chat reopens it; reload to show the "Reopened" event in place.
        if (conversation?.status === "RESOLVED") void load();
        onChanged?.();
      } catch (error) {
        const message = errorMessage(error);
        setItems((previous) =>
          previous
            .filter((i) => i.key !== pendingKey)
            .map((i) => (i.key === userKey ? { ...i, local: { status: "failed", clientId, error: message } } : i)),
        );
        if (error instanceof ApiError && RECONCILE_CODES.has(error.code)) {
          // The server may have finished the turn even though the connection dropped.
          window.setTimeout(() => void load(), 1500);
        }
      } finally {
        busy.current = false;
        setSending(false);
      }
    },
    [conversation?.status, conversationId, items, load, onChanged],
  );

  const requestHandoff = useCallback(async () => {
    const result = await api.requestHandoff(conversationId);
    if (result.message) {
      const message = result.message;
      setItems((previous) => [...previous, { key: message.id, message }]);
    }
    setConversation((current) => (current ? { ...current, status: result.conversation_status, handoff_status: current.handoff_status === "ASSIGNED" ? "ASSIGNED" : "PENDING" } : current));
    onChanged?.();
  }, [conversationId, onChanged]);

  const close = useCallback(async () => {
    const updated = await api.closeConversation(conversationId);
    setConversation(updated);
    await load(); // pick up the "closed by" event added to the transcript
    onChanged?.();
  }, [conversationId, load, onChanged]);

  const rate = useCallback(
    async (messageId: string, rating: FeedbackRating, reason?: FeedbackReason | null, comment?: string | null) => {
      await api.sendFeedback(conversationId, { message_id: messageId, rating, reason: reason ?? null, comment: comment ?? null });
      setItems((previous) =>
        previous.map((i) => (i.message.id === messageId ? { ...i, message: { ...i.message, feedback: { rating, reason: reason ?? null, comment: comment ?? null } } } : i)),
      );
    },
    [conversationId],
  );

  return { conversation, items, loading, loadError, sending, send, requestHandoff, close, rate, reload: load };
}
