import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { homePathFor, useAuth } from "../auth/AuthContext";
import { Icon } from "../components/icons";
import { Logo } from "../components/Logo";
import { Alert, Button, Field, Input } from "../components/ui";
import { errorMessage } from "../lib/api";

const DEV_ACCOUNTS = [
  { role: "Admin", email: "admin@acme.example", password: "AcmeAdmin!2026" },
  { role: "Support agent", email: "agent@acme.example", password: "AcmeAgent!2026" },
  { role: "Customer", email: "customer@acme.example", password: "AcmeCustomer!2026" },
];

export function LoginPage() {
  const { user, login, sessionExpired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={homePathFor(user)} replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await login(email.trim(), password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : homePathFor(me), { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-slate-900 p-12 text-white lg:flex">
        <Logo inverted />
        <div className="relative z-10 max-w-md">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">Answers from our documentation. A person when you need one.</h1>
          <ul className="mt-8 space-y-4 text-sm text-slate-300">
            {[
              ["book", "Every answer cites the policy or guide it came from."],
              ["shield", "If our documentation doesn't cover it, the assistant says so instead of guessing."],
              ["handoff", "Connect with a support representative at any time."],
            ].map(([icon, text]) => (
              <li key={text} className="flex gap-3">
                <span className="mt-0.5 text-brand-200"><Icon name={icon as "book"} size={18} /></span>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-slate-500">Acme is a fictional company used for demonstration.</p>
        <div className="pointer-events-none absolute -right-24 -top-24 h-96 w-96 rounded-full bg-brand-600/30 blur-3xl" aria-hidden="true" />
      </aside>

      <main className="flex items-center justify-center px-5 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden"><Logo /></div>
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h2>
          <p className="mt-1 text-sm text-slate-500">Use your Acme Support account.</p>

          <form onSubmit={onSubmit} className="mt-8 space-y-5" noValidate>
            {sessionExpired && <Alert tone="amber">Your session expired. Please sign in again.</Alert>}
            {error && <Alert tone="red">{error}</Alert>}
            <Field label="Email" htmlFor="email">
              <Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Password" htmlFor="password">
              <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <Button type="submit" className="w-full" loading={busy} disabled={!email || !password}>
              Sign in
            </Button>
          </form>

          {import.meta.env.DEV && (
            <div className="mt-10 rounded-xl border border-dashed border-slate-300 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Local development accounts</p>
              <ul className="mt-3 space-y-1.5">
                {DEV_ACCOUNTS.map((account) => (
                  <li key={account.email}>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-100"
                      onClick={() => {
                        setEmail(account.email);
                        setPassword(account.password);
                      }}
                    >
                      <span className="text-slate-700">{account.role}</span>
                      <span className="font-mono text-xs text-slate-500">{account.email}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-slate-400">Shown only in development builds. Never use these credentials in production.</p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
