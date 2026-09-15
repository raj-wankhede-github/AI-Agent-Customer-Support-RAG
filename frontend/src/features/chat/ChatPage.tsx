import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import { Icon, type IconName } from "../../components/icons";
import { LogoMark } from "../../components/Logo";
import { Alert, IconButton } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { Composer } from "./Composer";
import { ConversationSidebar } from "./ConversationSidebar";
import { ConversationView } from "./ConversationView";

const SUGGESTIONS: { icon: IconName; text: string }[] = [
  { icon: "clock", text: "What is your standard shipping time?" },
  { icon: "refresh", text: "What is your refund policy?" },
  { icon: "lock", text: "How do I reset my password?" },
  { icon: "settings", text: "How do I factory reset my SmartHub?" },
];

function Welcome({ onOpenSidebar, onCreated }: { onOpenSidebar: () => void; onCreated: () => void }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(text: string) {
    setBusy(true);
    setError(null);
    try {
      // Asking the same question as a recent open chat continues that chat instead of duplicating it.
      const conversation = await api.createConversation(text);
      onCreated();
      navigate(`/chat/${conversation.id}`, { state: { initialMessage: text } });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <header className="flex items-center border-b border-slate-200 px-3 py-2.5 lg:hidden">
        <IconButton icon="menu" label="Open conversation history" onClick={onOpenSidebar} />
      </header>
      <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-4 py-10">
        <div className="w-full max-w-2xl">
          <div className="mb-8 flex flex-col items-center text-center">
            <LogoMark size={44} />
            <h1 className="mt-4 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
              Hi{user ? `, ${user.name.split(" ")[0]}` : ""}. How can we help?
            </h1>
            <p className="mt-2 max-w-md text-sm text-slate-500">
              Answers come from our support documentation, with sources. If it isn't covered, we'll say so and connect you with a person.
            </p>
          </div>
          <div className="mb-6 grid gap-2 sm:grid-cols-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s.text}
                type="button"
                disabled={busy}
                onClick={() => void start(s.text)}
                className="flex items-center gap-3 rounded-xl border border-slate-200 px-4 py-3 text-left text-sm text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 disabled:opacity-60"
              >
                <span className="text-brand-600"><Icon name={s.icon} size={18} /></span>
                {s.text}
              </button>
            ))}
          </div>
          {error && <div className="mb-3"><Alert tone="red">{error}</Alert></div>}
          <Composer onSend={(text) => void start(text)} busy={busy} autoFocus />
        </div>
      </div>
    </div>
  );
}

export function ChatPage() {
  const { conversationId } = useParams();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <div className="flex h-full">
      <ConversationSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} refreshKey={refreshKey} />
      <main className="flex h-full min-w-0 flex-1">
        {conversationId ? (
          <ConversationView key={conversationId} conversationId={conversationId} onOpenSidebar={() => setSidebarOpen(true)} onChanged={refresh} />
        ) : (
          <Welcome onOpenSidebar={() => setSidebarOpen(true)} onCreated={refresh} />
        )}
      </main>
    </div>
  );
}
