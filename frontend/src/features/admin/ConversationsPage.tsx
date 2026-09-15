import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ConversationStatusBadge, HandoffStatusBadge } from "../../components/StatusBadge";
import { Icon } from "../../components/icons";
import { Alert, Badge, Card, EmptyState, Input, Pagination, Select, Skeleton } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { closedLabel, relativeTime, resolvedLabel } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import type { AdminConversation, ConversationStatus, HandoffStatus, Page } from "../../lib/types";
import { PageHeader } from "./AdminLayout";

export default function ConversationsPage() {
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim());
  const [status, setStatus] = useState<ConversationStatus | "">("");
  const [handoff, setHandoff] = useState<HandoffStatus | "">("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<AdminConversation> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .adminConversations({ page, q: q || undefined, status: status || undefined, handoff_status: handoff || undefined })
      .then((result) => !cancelled && (setData(result), setError(null)))
      .catch((err) => !cancelled && setError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [page, q, status, handoff]);

  return (
    <div>
      <PageHeader title="Conversations" description="Every customer conversation in your organization." />
      <div className="space-y-4 px-4 py-6 sm:px-8">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <label htmlFor="conversation-q" className="sr-only">Search</label>
            <Input id="conversation-q" type="search" placeholder="Search titles and messages" className="pl-9" value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }} />
          </div>
          <label className="sr-only" htmlFor="status-filter">Status</label>
          <Select id="status-filter" className="sm:w-52" value={status} onChange={(e) => { setStatus(e.target.value as ConversationStatus | ""); setPage(1); }}>
            <option value="">All statuses</option>
            <option value="OPEN">Open</option>
            <option value="WAITING_FOR_HUMAN">Waiting for support</option>
            <option value="WAITING_FOR_CUSTOMER">Waiting for customer</option>
            <option value="RESOLVED">Resolved</option>
            <option value="CLOSED">Closed</option>
          </Select>
          <label className="sr-only" htmlFor="handoff-filter">Handoff</label>
          <Select id="handoff-filter" className="sm:w-44" value={handoff} onChange={(e) => { setHandoff(e.target.value as HandoffStatus | ""); setPage(1); }}>
            <option value="">Any handoff</option>
            <option value="PENDING">Pending</option>
            <option value="ASSIGNED">Assigned</option>
            <option value="RESOLVED">Resolved</option>
            <option value="NONE">No handoff</option>
          </Select>
        </div>

        {error && <Alert tone="red">{error}</Alert>}

        <Card className="overflow-hidden">
          {!data && <div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>}
          {data && data.items.length === 0 && <EmptyState icon="conversation" title="No conversations found" description="Try a different search or filter." />}
          {data && data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-3 font-medium">Conversation</th>
                    <th scope="col" className="px-4 py-3 font-medium">Customer</th>
                    <th scope="col" className="px-4 py-3 font-medium">Status</th>
                    <th scope="col" className="hidden px-4 py-3 font-medium md:table-cell">Messages</th>
                    <th scope="col" className="px-4 py-3 font-medium">Last activity</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((c) => (
                    <tr key={c.id} className="hover:bg-slate-50">
                      <td className="max-w-xs px-4 py-3">
                        <Link to={`/admin/conversations/${c.id}`} className="block truncate font-medium text-slate-900 hover:text-brand-700">{c.title}</Link>
                        <span className="block truncate text-xs text-slate-500">{c.last_message_preview}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="block text-slate-800">{c.customer.name}</span>
                        <span className="block text-xs text-slate-500">{c.customer.email}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          <ConversationStatusBadge status={c.status} staff />
                          <HandoffStatusBadge status={c.handoff_status} />
                          {c.reopen_count > 0 && c.status !== "RESOLVED" && c.status !== "CLOSED" && (
                            <Badge tone="violet">Reopened{c.reopen_count > 1 ? ` ×${c.reopen_count}` : ""}</Badge>
                          )}
                        </div>
                        {c.status === "CLOSED" && <span className="mt-1 block text-xs text-slate-500">{closedLabel(c)}</span>}
                        {c.status === "RESOLVED" && <span className="mt-1 block text-xs text-slate-500">{resolvedLabel(c)}</span>}
                        {c.reopen_count > 0 && c.reopened_at && c.status !== "RESOLVED" && c.status !== "CLOSED" && (
                          <span className="mt-1 block text-xs text-slate-500">Reopened by customer {relativeTime(c.reopened_at)}</span>
                        )}
                      </td>
                      <td className="hidden px-4 py-3 text-slate-600 md:table-cell">{c.message_count}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-600">{relativeTime(c.last_message_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && <Pagination page={data.page} pages={data.pages} total={data.total} onChange={setPage} />}
        </Card>
      </div>
    </div>
  );
}
