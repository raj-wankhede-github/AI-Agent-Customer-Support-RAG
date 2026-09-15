import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import { Icon, type IconName } from "../../components/icons";
import { Logo } from "../../components/Logo";
import { Badge, IconButton } from "../../components/ui";
import { api } from "../../lib/api";
import { usePolling } from "../../lib/hooks";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  adminOnly?: boolean;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/admin", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/admin/handoffs", label: "Handoff queue", icon: "handoff" },
  { to: "/admin/conversations", label: "Conversations", icon: "conversation" },
  { to: "/admin/documents", label: "Knowledge base", icon: "document", adminOnly: true },
  { to: "/admin/retrieval", label: "Retrieval debugger", icon: "bug", adminOnly: true },
];

export default function AdminLayout() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const location = useLocation();

  const refreshQueue = useCallback(() => {
    api.handoffs({ status: "PENDING" }).then((page) => setPending(page.total)).catch(() => undefined);
  }, []);
  useEffect(refreshQueue, [refreshQueue, location.pathname]);
  usePolling(refreshQueue, 30_000);

  const items = NAV.filter((item) => !item.adminOnly || user?.role === "ADMIN");

  return (
    <div className="flex h-full">
      {open && <div className="fixed inset-0 z-30 bg-slate-900/50 lg:hidden" onClick={() => setOpen(false)} aria-hidden="true" />}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-slate-900 text-slate-300 transition-transform lg:static lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center justify-between px-4 py-4">
          <Logo inverted />
          <button type="button" className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 lg:hidden" aria-label="Close menu" onClick={() => setOpen(false)}>
            <Icon name="close" size={18} />
          </button>
        </div>
        <p className="px-5 pb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Support console</p>
        <nav className="flex-1 space-y-0.5 px-3" aria-label="Admin">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${isActive ? "bg-slate-800 text-white" : "hover:bg-slate-800/60 hover:text-white"}`
              }
            >
              <Icon name={item.icon} size={18} />
              <span className="flex-1">{item.label}</span>
              {item.to === "/admin/handoffs" && pending ? (
                <span className="rounded-full bg-amber-400 px-1.5 py-0.5 text-[11px] font-semibold text-slate-900" aria-label={`${pending} pending`}>
                  {pending}
                </span>
              ) : null}
            </NavLink>
          ))}
          <div className="my-3 border-t border-slate-800" />
          <NavLink to="/chat" className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-slate-800/60 hover:text-white">
            <Icon name="conversation" size={18} /> Customer chat
          </NavLink>
        </nav>
        <div className="border-t border-slate-800 p-3">
          <div className="flex items-center gap-3 px-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white" aria-hidden="true">
              {user?.name.slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-white">{user?.name}</p>
              <p className="truncate text-xs text-slate-500">{user?.role === "ADMIN" ? "Administrator" : "Support agent"} · {user?.company_name}</p>
            </div>
            <button type="button" aria-label="Sign out" title="Sign out" onClick={() => void logout()} className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white">
              <Icon name="logout" size={17} />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 lg:hidden">
          <IconButton icon="menu" label="Open menu" onClick={() => setOpen(true)} />
          <span className="text-sm font-semibold text-slate-900">Support console</span>
        </header>
        <main className="flex-1 overflow-y-auto bg-slate-50">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions, badge }: { title: string; description?: string; actions?: ReactNode; badge?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 border-b border-slate-200 bg-white px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
          {badge}
        </div>
        {description && <p className="mt-0.5 text-sm text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function RoleBadge({ role }: { role: string }) {
  return <Badge tone={role === "ADMIN" ? "violet" : role === "AGENT" ? "blue" : "gray"}>{role.toLowerCase()}</Badge>;
}
