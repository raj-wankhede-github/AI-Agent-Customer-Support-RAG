import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Icon, type IconName } from "../../components/icons";
import { Alert, Button, Card, Skeleton } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { humanize, percent, relativeTime } from "../../lib/format";
import { usePolling } from "../../lib/hooks";
import type { CountItem, Metrics } from "../../lib/types";
import { PageHeader } from "./AdminLayout";

// Single-series magnitude bars use one hue (reference palette blue, step 450).
const BAR_COLOR = "#2a78d6";
const WINDOWS = [7, 30, 90] as const;

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

function StatTile({ label, value, detail, icon, to }: { label: string; value: string; detail?: string; icon: IconName; to?: string }) {
  const body = (
    <>
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-600">{label}</p>
        <span className="text-slate-400"><Icon name={icon} size={18} /></span>
      </div>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">{value}</p>
      {detail && <p className="mt-1 text-xs text-slate-500">{detail}</p>}
    </>
  );
  return to ? (
    <Link to={to} className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition-colors hover:border-slate-300">{body}</Link>
  ) : (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">{body}</div>
  );
}

/** Horizontal single-series bar list: label on the left, value at the bar tip, share on hover. */
function BarList({ title, description, items, empty }: { title: string; description?: string; items: CountItem[]; empty: string }) {
  const max = Math.max(1, ...items.map((i) => i.count));
  const total = items.reduce((sum, i) => sum + i.count, 0);
  return (
    <Card className="p-4">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      {items.length === 0 ? (
        <p className="mt-6 pb-4 text-center text-sm text-slate-500">{empty}</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {items.map((item) => {
            const share = total ? item.count / total : 0;
            return (
              <li key={item.key} className="group relative">
                <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                  <span className="truncate text-slate-700">{humanize(item.key)}</span>
                  <span className="sr-only">{`${item.count} (${percent(share)})`}</span>
                </div>
                <div className="flex items-center gap-2" aria-hidden="true">
                  <div className="h-2 flex-1">
                    <div
                      className="h-2 rounded-r-[4px]"
                      style={{ width: `${Math.max(2, (item.count / max) * 100)}%`, backgroundColor: BAR_COLOR }}
                    />
                  </div>
                  <span className="w-10 text-right text-sm font-medium tabular-nums text-slate-900">{item.count}</span>
                </div>
                <div className="pointer-events-none absolute -top-8 right-0 z-10 hidden rounded-md bg-slate-900 px-2 py-1 text-xs text-white shadow group-hover:block" aria-hidden="true">
                  {humanize(item.key)}: {item.count} ({percent(share)})
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}</div>
      <div className="grid gap-4 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-56 rounded-xl" />)}</div>
    </div>
  );
}

export default function DashboardPage() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.metrics(days).then((m) => { setMetrics(m); setError(null); }).catch((err) => setError(errorMessage(err)));
  }, [days]);
  useEffect(load, [load]);
  usePolling(load, 30_000);

  const m = metrics;
  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="How the assistant and the support team are doing. Rates are measured from real conversations, not estimated."
        actions={
          <div role="radiogroup" aria-label="Time window" className="inline-flex rounded-lg border border-slate-200 bg-white p-1">
            {WINDOWS.map((w) => (
              <button key={w} type="button" role="radio" aria-checked={days === w} onClick={() => setDays(w)}
                className={`rounded-md px-3 py-1 text-sm font-medium ${days === w ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>
                {w}d
              </button>
            ))}
          </div>
        }
      />
      <div className="space-y-6 px-4 py-6 sm:px-8">
        {error && <Alert tone="red" action={<Button size="sm" variant="secondary" onClick={load}>Retry</Button>}>{error}</Alert>}
        {!m && !error && <DashboardSkeleton />}
        {m && (
          <>
            <section aria-label="Conversations" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile icon="conversation" label="Conversations" value={compact.format(m.conversations_total)}
                detail={`${m.conversations_open} open · ${m.conversations_resolved + m.conversations_closed} resolved or closed`} to="/admin/conversations" />
              <StatTile icon="handoff" label="Waiting for a person" value={compact.format(m.pending_handoffs)}
                detail={`Handoff rate ${percent(m.handoff_rate)} of conversations`} to="/admin/handoffs" />
              <StatTile icon="shield" label="Abstention rate" value={percent(m.abstention_rate)}
                detail={`${m.abstained} of ${m.assistant_responses} assistant responses`} />
              <StatTile icon="clock" label="Average response time" value={m.avg_response_latency_ms === null ? "-" : `${(m.avg_response_latency_ms / 1000).toFixed(1)}s`}
                detail={m.p95_response_latency_ms === null ? "No responses yet" : `p95 ${(m.p95_response_latency_ms / 1000).toFixed(1)}s`} />
            </section>

            <section aria-label="Quality and knowledge base" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile icon="book" label="Retrieval success" value={percent(m.retrieval_success_rate)}
                detail="Questions where sufficient evidence was found" />
              <StatTile icon="alert" label="Validation failures" value={compact.format(m.citation_validation_failures)}
                detail={`${m.low_confidence_responses} low-confidence responses withheld`} />
              <StatTile icon="thumbUp" label="Helpful feedback" value={m.feedback_helpful + m.feedback_not_helpful ? percent(m.feedback_helpful / (m.feedback_helpful + m.feedback_not_helpful)) : "-"}
                detail={`${m.feedback_helpful} helpful · ${m.feedback_not_helpful} not helpful`} />
              <StatTile icon="document" label="Searchable documents" value={compact.format(m.documents_searchable)}
                detail={`${m.documents_inactive} inactive · ${m.ingestion_failures} failed versions`} to="/admin/documents" />
            </section>

            <section aria-label="Breakdowns" className="grid gap-4 lg:grid-cols-3">
              <BarList title="Handoff reasons" description="Why conversations went to a person" items={m.handoff_reasons} empty="No handoffs in this period." />
              <BarList title="Abstention reasons" description="Why the assistant declined to answer" items={m.abstention_reasons} empty="No abstentions in this period." />
              <BarList title="Not-helpful feedback" description="Reasons customers gave" items={m.feedback_reasons} empty="No negative feedback in this period." />
            </section>

            <div className="grid gap-4 lg:grid-cols-3">
              <Card className="overflow-hidden lg:col-span-2">
                <div className="px-4 pt-4">
                  <h2 className="text-sm font-semibold text-slate-900">Recent unanswered questions</h2>
                  <p className="mt-0.5 text-xs text-slate-500">Candidates for new or improved knowledge-base content</p>
                </div>
                {m.recent_unanswered.length === 0 ? (
                  <p className="p-6 text-center text-sm text-slate-500">Every recent question was answered.</p>
                ) : (
                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full text-left text-sm">
                      <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th scope="col" className="px-4 py-2 font-medium">Question</th>
                          <th scope="col" className="px-4 py-2 font-medium">Outcome</th>
                          <th scope="col" className="px-4 py-2 font-medium">When</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {m.recent_unanswered.map((q) => (
                          <tr key={q.trace_id}>
                            <td className="max-w-md px-4 py-2.5">
                              <Link to={`/admin/conversations/${q.conversation_id}`} className="block truncate text-slate-900 hover:text-brand-700">{q.question}</Link>
                              <span className="block truncate text-xs text-slate-500">{q.reason}</span>
                            </td>
                            <td className="whitespace-nowrap px-4 py-2.5 text-slate-700">{humanize(q.decision)}</td>
                            <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{relativeTime(q.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
              <BarList title="Document versions" description="Ingestion status across the knowledge base" items={m.versions_by_status} empty="No documents uploaded yet." />
            </div>

            <p className="text-xs text-slate-500">
              Model usage in this period: {compact.format(m.input_tokens)} input and {compact.format(m.output_tokens)} output tokens.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
