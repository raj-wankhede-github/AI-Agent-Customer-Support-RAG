import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import { Icon } from "../../components/icons";
import { Logo } from "../../components/Logo";
import { Spinner } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { relativeTime } from "../../lib/format";
import type { Conversation, ConversationStatus } from "../../lib/types";

const STATUS_DOT: Record<ConversationStatus, string> = {
  OPEN: "bg-sky-400",
  WAITING_FOR_CUSTOMER: "bg-brand-400",
  WAITING_FOR_HUMAN: "bg-amber-400",
  RESOLVED: "bg-emerald-400",
  CLOSED: "bg-slate-500",
};
const STATUS_LABEL: Record<ConversationStatus, string> = {
  OPEN: "Open",
  WAITING_FOR_CUSTOMER: "Support replied",
  WAITING_FOR_HUMAN: "Waiting for support",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
};

export function ConversationSidebar({ open, onClose, refreshKey }: { open: boolean; onClose: () => void; refreshKey: number }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [items, setItems] = useState<Conversation[]>([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listConversations({ page: 1, q: debounced || undefined })
      .then((result) => {
        if (cancelled) return;
        setItems(result.items);
        setPage(1);
        setPages(result.pages);
        setError(null);
      })
      .catch((err) => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [debounced, refreshKey]);

  async function loadMore() {
    setLoading(true);
    try {
      const result = await api.listConversations({ page: page + 1, q: debounced || undefined });
      setItems((current) => [...current, ...result.items.filter((c) => !current.some((x) => x.id === c.id))]);
      setPage(result.page);
      setPages(result.pages);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-slate-900/50 lg:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col bg-slate-900 text-slate-200 transition-transform lg:static lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}
        aria-label="Conversation history"
      >
        <div className="flex items-center justify-between px-4 pb-2 pt-4">
          <Link to="/chat" onClick={onClose} aria-label="Acme Support home"><Logo inverted /></Link>
          <button type="button" className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 lg:hidden" aria-label="Close menu" onClick={onClose}>
            <Icon name="close" size={18} />
          </button>
        </div>

        <div className="space-y-3 px-3 pt-3">
          <button
            type="button"
            onClick={() => {
              navigate("/chat");
              onClose();
            }}
            className="flex w-full items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            <Icon name="plus" size={16} /> New conversation
          </button>
          <div className="relative">
            <Icon name="search" size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <label htmlFor="conversation-search" className="sr-only">Search conversations</label>
            <input
              id="conversation-search"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search conversations"
              className="w-full rounded-lg bg-slate-800 py-2 pl-8 pr-3 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
        </div>

        <nav className="mt-3 flex-1 overflow-y-auto px-2 pb-3" aria-label="Recent conversations">
          <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{debounced ? "Search results" : "Recent"}</p>
          {error && <p className="px-2 py-2 text-xs text-red-300">{error}</p>}
          {!loading && !error && items.length === 0 && (
            <p className="px-2 py-3 text-sm text-slate-500">{debounced ? "No conversations match your search." : "No conversations yet."}</p>
          )}
          <ul className="space-y-0.5">
            {items.map((c) => (
              <li key={c.id}>
                <NavLink
                  to={`/chat/${c.id}`}
                  onClick={onClose}
                  className={({ isActive }) => `block rounded-lg px-2.5 py-2 hover:bg-slate-800 ${isActive ? "bg-slate-800" : ""}`}
                >
                  <span className="flex items-center gap-2">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[c.status]}`} aria-hidden="true" />
                    <span className="truncate text-sm text-slate-100">{c.title}</span>
                  </span>
                  <span className="mt-0.5 flex items-center justify-between gap-2 pl-3.5 text-xs text-slate-500">
                    <span className="truncate">{c.last_message_preview ?? STATUS_LABEL[c.status]}</span>
                    <time dateTime={c.last_message_at} className="shrink-0">{relativeTime(c.last_message_at)}</time>
                  </span>
                  <span className="sr-only">Status: {STATUS_LABEL[c.status]}</span>
                </NavLink>
              </li>
            ))}
          </ul>
          {loading && <div className="flex justify-center py-3 text-slate-400"><Spinner size={18} label="Loading conversations" /></div>}
          {!loading && page < pages && (
            <button type="button" onClick={loadMore} className="mt-2 w-full rounded-lg px-2 py-2 text-xs font-medium text-slate-400 hover:bg-slate-800 hover:text-slate-200">
              Load more
            </button>
          )}
        </nav>

        <div className="border-t border-slate-800 p-3">
          {user && user.role !== "CUSTOMER" && (
            <Link to="/admin" className="mb-2 flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-slate-300 hover:bg-slate-800">
              <Icon name="dashboard" size={16} /> Support console
            </Link>
          )}
          <div className="flex items-center gap-3 px-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-700 text-xs font-semibold text-white" aria-hidden="true">
              {user?.name.slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-white">{user?.name}</p>
              <p className="truncate text-xs text-slate-500">{user?.email}</p>
            </div>
            <button type="button" aria-label="Sign out" title="Sign out" onClick={() => void logout()} className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white">
              <Icon name="logout" size={17} />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
