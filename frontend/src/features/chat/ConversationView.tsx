import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { Icon } from "../../components/icons";
import { ConversationStatusBadge } from "../../components/StatusBadge";
import { Alert, Button, ConfirmDialog, EmptyState, IconButton, Skeleton } from "../../components/ui";
import { useAuth } from "../../auth/AuthContext";
import { errorMessage } from "../../lib/api";
import { closedLabel, resolvedLabel } from "../../lib/format";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { humanInvolved, useConversation } from "./useConversation";

export function ConversationView({ conversationId, onOpenSidebar, onChanged }: { conversationId: string; onOpenSidebar: () => void; onChanged: () => void }) {
  const { conversation, items, loading, loadError, sending, send, requestHandoff, close, rate, reload } = useConversation(conversationId, onChanged);
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const initialSent = useRef(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // A first message typed on the welcome screen is carried here in navigation state.
  useEffect(() => {
    const initial = (location.state as { initialMessage?: string } | null)?.initialMessage;
    if (initial && !loading && conversation && !initialSent.current) {
      initialSent.current = true;
      navigate(location.pathname, { replace: true, state: null });
      void send(initial);
    }
  }, [location, loading, conversation, navigate, send]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  async function runAction(action: () => Promise<void>) {
    setActionBusy(true);
    setActionError(null);
    try {
      await action();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActionBusy(false);
    }
  }

  const closed = conversation?.status === "CLOSED";
  const withHuman = humanInvolved(conversation);
  const canRequestHuman = !!conversation && !closed && !withHuman;

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <header className="flex items-center gap-2 border-b border-slate-200 px-3 py-2.5 sm:px-5">
        <IconButton icon="menu" label="Open conversation history" className="lg:hidden" onClick={onOpenSidebar} />
        <div className="min-w-0 flex-1">
          {conversation ? (
            <div className="flex min-w-0 items-center gap-2">
              <h1 className="truncate text-[15px] font-semibold text-slate-900">{conversation.title}</h1>
              <span className="hidden sm:inline-flex"><ConversationStatusBadge status={conversation.status} /></span>
            </div>
          ) : (
            <Skeleton className="h-5 w-48" />
          )}
        </div>
        {canRequestHuman && (
          <Button variant="secondary" size="sm" icon="handoff" loading={actionBusy} onClick={() => runAction(requestHandoff)}>
            <span className="hidden sm:inline">Talk to a human</span>
            <span className="sm:hidden">Human</span>
          </Button>
        )}
        {conversation && !closed && (
          <Button variant="ghost" size="sm" onClick={() => setConfirmClose(true)}>Close</Button>
        )}
      </header>

      <div ref={scroller} onScroll={onScroll} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6 sm:px-6">
          {loading && (
            <div className="space-y-6" aria-label="Loading conversation">
              <Skeleton className="ml-auto h-12 w-2/3 rounded-2xl" />
              <Skeleton className="h-24 w-4/5 rounded-2xl" />
            </div>
          )}
          {loadError && (
            loadError.status === 404 ? (
              <EmptyState icon="conversation" title="Conversation not found" description="It may have been deleted, or it belongs to another account."
                action={<Button onClick={() => navigate("/chat")}>Start a new conversation</Button>} />
            ) : (
              <Alert tone="red" title="We couldn't load this conversation" action={<Button size="sm" variant="secondary" onClick={() => void reload()}>Retry</Button>}>
                {loadError.message}
              </Alert>
            )
          )}
          {!loading && !loadError && items.length === 0 && (
            <EmptyState icon="conversation" title="No messages yet" description="Ask a question below to get started." />
          )}
          {items.map((item) => (
            <MessageBubble
              key={item.key}
              item={item}
              canRequestHuman={canRequestHuman}
              onRetry={(key) => {
                const failed = items.find((i) => i.key === key);
                if (failed) void send(failed.message.content, key);
              }}
              onRequestHuman={() => void runAction(requestHandoff)}
              onRate={rate}
            />
          ))}
        </div>
      </div>

      <div className="border-t border-slate-100 bg-white px-4 pb-4 pt-3 sm:px-6">
        <div className="mx-auto w-full max-w-3xl space-y-2">
          {actionError && <Alert tone="red">{actionError}</Alert>}
          {conversation?.handoff_status === "PENDING" && !closed && (
            <Alert tone="blue" title="You're in the queue for a support representative">
              They'll see this whole conversation. You can keep adding details below.
            </Alert>
          )}
          {conversation?.status === "RESOLVED" && (
            <Alert tone="green" title="Support marked this conversation resolved">
              {resolvedLabel(conversation, user?.id)}. Still need help? Reply here and it will reopen.
            </Alert>
          )}
          {conversation?.handoff_status === "ASSIGNED" && !closed && (
            <p className="flex items-center gap-2 text-xs text-emerald-700"><Icon name="handoff" size={14} /> A support representative is handling this conversation.</p>
          )}
          {closed ? (
            <Alert tone="amber" title={`This conversation is closed · ${closedLabel(conversation!, user?.id)}`} action={<Button size="sm" onClick={() => navigate("/chat")}>New conversation</Button>}>
              It stays in your history, but it can't receive new messages.
            </Alert>
          ) : (
            <Composer
              onSend={(text) => {
                stickToBottom.current = true;
                void send(text);
              }}
              busy={sending}
              disabled={!conversation}
              placeholder={
                conversation?.status === "RESOLVED"
                  ? "Reply to reopen this conversation…"
                  : withHuman
                    ? "Add a message for the support team…"
                    : "Ask a question…"
              }
            />
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmClose}
        title="Close this conversation?"
        message="You'll still be able to read it in your history, but you won't be able to send new messages in it."
        confirmLabel="Close conversation"
        busy={actionBusy}
        onClose={() => setConfirmClose(false)}
        onConfirm={() => runAction(async () => { await close(); setConfirmClose(false); })}
      />
    </div>
  );
}
