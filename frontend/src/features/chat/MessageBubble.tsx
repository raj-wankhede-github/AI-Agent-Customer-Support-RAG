import { Icon } from "../../components/icons";
import { Button } from "../../components/ui";
import { dateTime } from "../../lib/format";
import type { FeedbackRating, FeedbackReason } from "../../lib/types";
import { CitationList } from "./CitationList";
import { FeedbackControls } from "./FeedbackControls";
import type { ChatItem } from "./useConversation";

const STAGE_LABEL: Record<string, string> = {
  processing: "Searching our documentation…",
};

function Paragraphs({ text }: { text: string }) {
  return (
    <div className="space-y-2 whitespace-pre-wrap break-words text-[15px] leading-relaxed">
      {text.split(/\n{2,}/).map((paragraph, i) => (
        <p key={i}>{paragraph}</p>
      ))}
    </div>
  );
}

function Avatar({ kind }: { kind: "assistant" | "human" }) {
  return (
    <div
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${kind === "assistant" ? "bg-brand-600 text-white" : "bg-emerald-600 text-white"}`}
      aria-hidden="true"
    >
      <Icon name={kind === "assistant" ? "bot" : "user"} size={17} />
    </div>
  );
}

function TypingIndicator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-500" role="status">
      <span className="flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span key={i} className="typing-dot h-1.5 w-1.5 rounded-full bg-slate-400" style={{ animationDelay: `${i * 0.15}s` }} />
        ))}
      </span>
      {label}
    </div>
  );
}

export function MessageBubble({
  item,
  canRequestHuman,
  onRetry,
  onRequestHuman,
  onRate,
}: {
  item: ChatItem;
  canRequestHuman: boolean;
  onRetry: (key: string) => void;
  onRequestHuman: () => void;
  onRate: (messageId: string, rating: FeedbackRating, reason?: FeedbackReason | null, comment?: string | null) => Promise<void>;
}) {
  const { message, local } = item;

  if (message.role === "USER") {
    const failed = local?.status === "failed";
    return (
      <div className="flex flex-col items-end">
        <div className={`max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 sm:max-w-[75%] ${failed ? "bg-red-50 text-red-900 ring-1 ring-red-200" : "bg-brand-600 text-white"}`}>
          <Paragraphs text={message.content} />
        </div>
        {local?.status === "sending" && <span className="mt-1 text-xs text-slate-400">Sending…</span>}
        {failed && (
          <div className="mt-1.5 flex items-center gap-2 text-xs text-red-700" role="alert">
            <Icon name="alert" size={14} />
            <span>{local.error}</span>
            <Button size="sm" variant="ghost" icon="refresh" onClick={() => onRetry(item.key)}>Retry</Button>
          </div>
        )}
      </div>
    );
  }

  if (message.role === "HUMAN_AGENT") {
    return (
      <div className="flex gap-3">
        <Avatar kind="human" />
        <div className="min-w-0 max-w-[85%] sm:max-w-[75%]">
          <p className="mb-1 text-xs font-medium text-emerald-700">{message.author_name ?? "Support representative"}</p>
          <div className="rounded-2xl rounded-tl-md bg-emerald-50 px-4 py-2.5 text-slate-900 ring-1 ring-emerald-100">
            <Paragraphs text={message.content} />
          </div>
          {message.id && <FeedbackControls feedback={message.feedback} onRate={(r, reason, c) => onRate(message.id, r, reason, c)} />}
        </div>
      </div>
    );
  }

  if (message.role === "SYSTEM") {
    return (
      <p className="flex items-center justify-center gap-2 text-center text-xs text-slate-500" role="note">
        <span className="h-px w-8 bg-slate-200" aria-hidden="true" />
        <span>
          {message.content} · <time dateTime={message.created_at}>{dateTime(message.created_at)}</time>
        </span>
        <span className="h-px w-8 bg-slate-200" aria-hidden="true" />
      </p>
    );
  }

  // ASSISTANT
  if (local?.status === "streaming" && !message.content) {
    return (
      <div className="flex gap-3">
        <Avatar kind="assistant" />
        <div className="pt-1.5"><TypingIndicator label={STAGE_LABEL[local.stage] ?? "Working on it…"} /></div>
      </div>
    );
  }

  const status = message.answer_status;
  const kind = message.response_kind;
  const isRagAnswer = status === "ANSWERED" && kind === "RAG_ANSWER";
  const abstained = status === "ABSTAINED";
  const handoff = status === "HANDOFF_REQUIRED";

  return (
    <div className="flex gap-3">
      <Avatar kind="assistant" />
      <div className="min-w-0 flex-1 sm:max-w-[85%]">
        <div
          className={`rounded-2xl rounded-tl-md px-4 py-3 ${
            abstained ? "bg-amber-50 ring-1 ring-amber-200" : handoff ? "bg-sky-50 ring-1 ring-sky-200" : "bg-slate-100"
          } text-slate-900`}
          aria-live={local?.status === "streaming" ? "polite" : undefined}
        >
          {abstained && (
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-amber-800">
              <Icon name="info" size={14} /> Not found in our documentation
            </p>
          )}
          {handoff && (
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-sky-800">
              <Icon name="handoff" size={14} /> Connecting you to a person
            </p>
          )}
          <Paragraphs text={message.content} />
          {local?.status === "streaming" && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-middle" aria-hidden="true" />}
          {abstained && canRequestHuman && !local && (
            <Button size="sm" variant="secondary" icon="handoff" className="mt-3" onClick={onRequestHuman}>
              Talk to a human
            </Button>
          )}
        </div>
        {isRagAnswer && message.confidence === "MEDIUM" && (
          <p className="mt-1.5 text-xs text-slate-500">Based on the sources below. Please check the details that matter to you.</p>
        )}
        <CitationList citations={message.citations} />
        {message.id && !local && (isRagAnswer || abstained) && (
          <FeedbackControls feedback={message.feedback} onRate={(r, reason, c) => onRate(message.id, r, reason, c)} />
        )}
      </div>
    </div>
  );
}
