import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import { Icon } from "../../components/icons";
import { AnswerStatusBadge, ConversationStatusBadge, HandoffStatusBadge, PriorityBadge } from "../../components/StatusBadge";
import { Alert, Badge, Button, Card, ConfirmDialog, EmptyState, Skeleton, Textarea } from "../../components/ui";
import { api, ApiError, errorMessage } from "../../lib/api";
import { closedLabel, dateTime, duration, humanize, resolvedLabel } from "../../lib/format";
import { usePolling } from "../../lib/hooks";
import type { AdminConversationDetail, Message } from "../../lib/types";
import { CitationList } from "../chat/CitationList";
import { PageHeader } from "./AdminLayout";
import { TraceInspector } from "./TraceInspector";

const ROLE_LABEL: Record<Message["role"], string> = { USER: "Customer", ASSISTANT: "AI assistant", HUMAN_AGENT: "Support", SYSTEM: "System" };

function TranscriptMessage({ message, selected, onInspect, hasTrace }: { message: Message; selected: boolean; onInspect: () => void; hasTrace: boolean }) {
  const styles = {
    USER: "bg-white ring-slate-200",
    ASSISTANT: "bg-slate-50 ring-slate-200",
    HUMAN_AGENT: "bg-emerald-50 ring-emerald-200",
    SYSTEM: "bg-white ring-slate-100",
  }[message.role];
  return (
    <li className={`rounded-xl p-4 ring-1 ${styles} ${selected ? "outline outline-2 outline-brand-500" : ""}`}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-slate-800">{message.role === "HUMAN_AGENT" && message.author_name ? message.author_name : ROLE_LABEL[message.role]}</span>
        <time className="text-slate-500" dateTime={message.created_at}>{dateTime(message.created_at)}</time>
        <AnswerStatusBadge status={message.answer_status} />
        {message.confidence && <Badge>Confidence: {humanize(message.confidence)}</Badge>}
        {message.handoff_reason && <Badge tone="blue">{humanize(message.handoff_reason)}</Badge>}
        {hasTrace && (
          <button type="button" onClick={onInspect} aria-pressed={selected} className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium text-brand-700 hover:bg-brand-50">
            <Icon name="bug" size={14} /> Inspect
          </button>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-800">{message.content}</p>
      <CitationList citations={message.citations} />
    </li>
  );
}

export default function AdminConversationPage() {
  const { conversationId = "" } = useParams();
  const { user } = useAuth();
  const [data, setData] = useState<AdminConversationDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);

  const load = useCallback(() => {
    api
      .adminConversation(conversationId)
      .then((detail) => {
        setData(detail);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err : new ApiError(0, "UNKNOWN", errorMessage(err))));
  }, [conversationId]);

  useEffect(load, [load]);
  usePolling(load, 5000, !!data && data.conversation.status !== "CLOSED" && busy === null);

  const traceByMessage = useMemo(() => new Map((data?.traces ?? []).filter((t) => t.assistant_message_id).map((t) => [t.assistant_message_id!, t])), [data]);
  const lastAssistant = data?.messages.filter((m) => traceByMessage.has(m.id)).at(-1)?.id ?? null;
  const inspected = traceByMessage.get(selectedMessage ?? lastAssistant ?? "");
  const openHandoff = data?.handoffs.find((h) => h.status === "PENDING" || h.status === "ASSIGNED");

  async function act(name: string, action: () => Promise<unknown>) {
    setBusy(name);
    setActionError(null);
    try {
      await action();
      load();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  function sendReply(event: FormEvent) {
    event.preventDefault();
    const text = reply.trim();
    if (!text) return;
    void act("reply", async () => {
      await api.agentReply(conversationId, text);
      setReply("");
    });
  }

  if (error) {
    return error.status === 404 ? (
      <EmptyState icon="conversation" title="Conversation not found" action={<Link to="/admin/conversations" className="text-sm font-medium text-brand-700">Back to conversations</Link>} />
    ) : (
      <div className="p-8"><Alert tone="red" title="Couldn't load the conversation" action={<Button size="sm" variant="secondary" onClick={load}>Retry</Button>}>{error.message}</Alert></div>
    );
  }
  if (!data) {
    return <div className="space-y-3 p-8"><Skeleton className="h-8 w-72" /><Skeleton className="h-40 w-full" /><Skeleton className="h-40 w-full" /></div>;
  }

  const { conversation } = data;
  const closed = conversation.status === "CLOSED";

  return (
    <div>
      <PageHeader
        title={conversation.title}
        description={[
          `${conversation.customer.name} · ${conversation.customer.email}`,
          closed ? closedLabel(conversation, user?.id) : null,
          conversation.status === "RESOLVED" ? resolvedLabel(conversation, user?.id) : null,
          conversation.reopen_count > 0 && conversation.reopened_at && !closed && conversation.status !== "RESOLVED"
            ? `Reopened by customer on ${dateTime(conversation.reopened_at)}`
            : null,
        ].filter(Boolean).join(" · ")}
        badge={
          <>
            <ConversationStatusBadge status={conversation.status} staff />
            <HandoffStatusBadge status={conversation.handoff_status} />
            {conversation.reopen_count > 0 && !closed && conversation.status !== "RESOLVED" && <Badge tone="violet">Reopened</Badge>}
          </>
        }
        actions={
          <>
            <Link to="/admin/conversations" className="inline-flex h-8 items-center gap-1 rounded-lg px-3 text-sm text-slate-600 hover:bg-slate-100">
              <Icon name="arrowLeft" size={16} /> Back
            </Link>
            {openHandoff && openHandoff.assigned_agent?.id !== user?.id && (
              <Button size="sm" variant="secondary" loading={busy === "assign"} onClick={() => act("assign", () => api.assignHandoff(openHandoff.id))}>Assign to me</Button>
            )}
            {!closed && conversation.status !== "RESOLVED" && (
              <Button size="sm" icon="check" loading={busy === "resolve"} onClick={() => act("resolve", () => api.resolveConversation(conversationId))}>Mark resolved</Button>
            )}
            {!closed && <Button size="sm" variant="ghost" onClick={() => setConfirmClose(true)}>Close</Button>}
          </>
        }
      />

      <div className="grid gap-6 px-4 py-6 sm:px-8 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-4">
          {actionError && <Alert tone="red">{actionError}</Alert>}
          <ol className="space-y-3" aria-label="Conversation history">
            {data.messages.map((m) => (
              <TranscriptMessage key={m.id} message={m} hasTrace={traceByMessage.has(m.id)} selected={inspected?.assistant_message_id === m.id} onInspect={() => setSelectedMessage(m.id)} />
            ))}
          </ol>

          <Card className="p-4">
            {closed ? (
              <p className="text-sm text-slate-500">This conversation is closed.</p>
            ) : (
              <form onSubmit={sendReply} className="space-y-2">
                <label htmlFor="agent-reply" className="text-sm font-medium text-slate-700">Reply to {conversation.customer.name}</label>
                <Textarea id="agent-reply" rows={3} maxLength={4000} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Write a reply. The customer sees it in their chat." />
                <div className="flex items-center justify-between">
                  <p className="text-xs text-slate-500">{openHandoff?.status === "PENDING" ? "Replying assigns this handoff to you." : "The AI assistant stays paused until the conversation is resolved."}</p>
                  <Button type="submit" icon="send" loading={busy === "reply"} disabled={!reply.trim()}>Send reply</Button>
                </div>
              </form>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <h2 className="px-4 pt-4 text-sm font-semibold text-slate-900">Handoffs</h2>
            {data.handoffs.length === 0 ? (
              <p className="px-4 pb-4 pt-1 text-sm text-slate-500">No handoffs for this conversation.</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {data.handoffs.map((h) => (
                  <li key={h.id} className="space-y-1 px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <PriorityBadge priority={h.priority} />
                      <HandoffStatusBadge status={h.status} />
                      <span className="text-xs text-slate-500">{h.status === "RESOLVED" ? `Resolved after ${duration(h.waiting_seconds)}` : `Waiting ${duration(h.waiting_seconds)}`}</span>
                    </div>
                    <p className="text-sm font-medium text-slate-800">{humanize(h.reason_code)}</p>
                    <p className="text-sm text-slate-600">{h.reason_detail}</p>
                    {h.assigned_agent && <p className="text-xs text-slate-500">Assigned to {h.assigned_agent.name}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <div className="flex items-center justify-between px-4 pt-4">
              <h2 className="text-sm font-semibold text-slate-900">AI diagnostics</h2>
              <span className="text-xs text-slate-500">Operational metadata only</span>
            </div>
            {inspected ? <TraceInspector trace={inspected} /> : <p className="px-4 pb-4 pt-1 text-sm text-slate-500">No AI responses in this conversation yet.</p>}
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirmClose}
        title="Close this conversation?"
        message="The customer will no longer be able to send messages in it. Open handoffs are resolved."
        confirmLabel="Close conversation"
        busy={busy === "close"}
        onClose={() => setConfirmClose(false)}
        onConfirm={() => act("close", async () => { await api.adminCloseConversation(conversationId); setConfirmClose(false); })}
      />
    </div>
  );
}
