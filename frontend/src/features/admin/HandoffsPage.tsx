import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import { HandoffStatusBadge, PriorityBadge } from "../../components/StatusBadge";
import { Alert, Button, Card, EmptyState, Pagination, Skeleton } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { duration, humanize, relativeTime } from "../../lib/format";
import { usePolling } from "../../lib/hooks";
import type { Handoff, Page } from "../../lib/types";
import { PageHeader } from "./AdminLayout";

type Tab = "OPEN" | "RESOLVED";

export default function HandoffsPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("OPEN");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Handoff> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .handoffs({ page, status: tab === "RESOLVED" ? "RESOLVED" : undefined })
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [page, tab]);

  useEffect(load, [load]);
  usePolling(load, 15_000);

  async function assignToMe(handoff: Handoff) {
    setBusyId(handoff.id);
    try {
      await api.assignHandoff(handoff.id);
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <PageHeader title="Handoff queue" description="Conversations waiting for a person, most urgent and longest-waiting first." />
      <div className="space-y-4 px-4 py-6 sm:px-8">
        <div role="tablist" aria-label="Handoff status" className="inline-flex rounded-lg border border-slate-200 bg-white p-1">
          {(["OPEN", "RESOLVED"] as Tab[]).map((value) => (
            <button
              key={value}
              role="tab"
              aria-selected={tab === value}
              type="button"
              onClick={() => {
                setTab(value);
                setPage(1);
                setData(null);
              }}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${tab === value ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            >
              {value === "OPEN" ? "Open" : "Resolved"}
            </button>
          ))}
        </div>

        {error && <Alert tone="red">{error}</Alert>}

        <Card>
          {!data && (
            <div className="space-y-3 p-4">
              {[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          )}
          {data && data.items.length === 0 && (
            <EmptyState icon="handoff" title={tab === "OPEN" ? "The queue is empty" : "No resolved handoffs yet"} description={tab === "OPEN" ? "New handoff requests will appear here automatically." : undefined} />
          )}
          {data && data.items.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {data.items.map((h) => {
                const mine = h.assigned_agent?.id === user?.id;
                return (
                  <li key={h.id} className="flex flex-col gap-3 p-4 lg:flex-row lg:items-center">
                    <div className="flex w-28 shrink-0 flex-row gap-2 lg:flex-col lg:items-start">
                      <PriorityBadge priority={h.priority} />
                      <HandoffStatusBadge status={h.status} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <Link to={`/admin/conversations/${h.conversation_id}`} className="block truncate text-sm font-semibold text-slate-900 hover:text-brand-700">
                        {h.conversation_title}
                      </Link>
                      <p className="truncate text-xs text-slate-500">
                        {h.customer.name} · {h.customer.email}
                      </p>
                      <p className="mt-1 text-sm text-slate-700">
                        <span className="font-medium">{humanize(h.reason_code)}</span>
                        <span className="text-slate-500"> - {h.reason_detail}</span>
                      </p>
                      {h.triggering_message_preview && <p className="mt-1 truncate text-xs italic text-slate-500">"{h.triggering_message_preview}"</p>}
                    </div>
                    <div className="flex shrink-0 items-center gap-4 lg:w-72 lg:justify-end">
                      <div className="text-right text-xs text-slate-500">
                        <p className="font-medium text-slate-700">{h.status === "RESOLVED" ? "Resolved" : `Waiting ${duration(h.waiting_seconds)}`}</p>
                        <p>{h.assigned_agent ? `Assigned to ${mine ? "you" : h.assigned_agent.name}` : `Requested ${relativeTime(h.created_at)}`}</p>
                      </div>
                      {h.status !== "RESOLVED" && !mine && (
                        <Button size="sm" variant="secondary" loading={busyId === h.id} onClick={() => assignToMe(h)}>Assign to me</Button>
                      )}
                      <Link to={`/admin/conversations/${h.conversation_id}`} className="inline-flex h-8 items-center rounded-lg bg-brand-600 px-3 text-sm font-medium text-white hover:bg-brand-700">
                        Open
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {data && <Pagination page={data.page} pages={data.pages} total={data.total} onChange={setPage} />}
        </Card>
      </div>
    </div>
  );
}
