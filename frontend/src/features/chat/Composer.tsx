import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Icon } from "../../components/icons";
import { Spinner } from "../../components/ui";

const MAX = 4000;

export function Composer({
  onSend,
  disabled = false,
  busy = false,
  placeholder = "Ask a question…",
  autoFocus = false,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  busy?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  useEffect(() => {
    if (autoFocus) area.current?.focus();
  }, [autoFocus]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const value = text.trim();
    if (!value || disabled || busy) return;
    onSend(value);
    setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  const nearLimit = text.length > MAX - 300;
  return (
    <form onSubmit={submit} className="relative">
      <label htmlFor="composer" className="sr-only">Message</label>
      <div className={`flex items-end gap-2 rounded-2xl border bg-white p-2 shadow-sm transition-colors focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20 ${disabled ? "border-slate-200 bg-slate-50" : "border-slate-300"}`}>
        <textarea
          id="composer"
          ref={area}
          rows={1}
          value={text}
          maxLength={MAX}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] text-slate-900 placeholder:text-slate-400 focus:outline-none disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          aria-label="Send message"
          disabled={disabled || busy || !text.trim()}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:bg-slate-200 disabled:text-slate-400"
        >
          {busy ? <Spinner size={18} /> : <Icon name="send" size={18} />}
        </button>
      </div>
      <div className="mt-1.5 flex justify-between px-1 text-[11px] text-slate-400">
        <span className="hidden sm:inline">Enter to send · Shift+Enter for a new line</span>
        <span className={nearLimit ? "text-amber-600" : ""}>{nearLimit ? `${text.length}/${MAX}` : "Answers come only from our support documentation."}</span>
      </div>
    </form>
  );
}
