import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Icon } from "../../components/icons";
import { ProcessingBadge } from "../../components/StatusBadge";
import { Alert, Button, Card, ConfirmDialog, EmptyState, IconButton, Input, Pagination, Select, Skeleton } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { humanize, relativeTime } from "../../lib/format";
import { useDebounced, usePolling } from "../../lib/hooks";
import type { DocumentStatus, KnowledgeDocument, Page } from "../../lib/types";
import { PageHeader } from "./AdminLayout";
import { UploadDialog } from "./UploadDialog";

const IN_PROGRESS = new Set(["UPLOADED", "PROCESSING"]);

export default function DocumentsPage() {
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim());
  const [status, setStatus] = useState<DocumentStatus | "">("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<KnowledgeDocument> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "green" | "blue"; text: string } | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [toDelete, setToDelete] = useState<KnowledgeDocument | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .documents({ page, q: q || undefined, status: status || undefined })
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [page, q, status]);

  useEffect(load, [load]);
  usePolling(load, 3000, !!data?.items.some((d) => IN_PROGRESS.has(d.processing_status)));

  async function run(id: string, action: () => Promise<unknown>, success: string) {
    setBusyId(id);
    setError(null);
    try {
      await action();
      setNotice({ tone: "green", text: success });
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Knowledge base"
        description="Only documents that finished processing (Ready) are used to answer customers."
        actions={<Button icon="upload" onClick={() => setUploadOpen(true)}>Upload document</Button>}
      />
      <div className="space-y-4 px-4 py-6 sm:px-8">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <label htmlFor="doc-q" className="sr-only">Search documents</label>
            <Input id="doc-q" type="search" className="pl-9" placeholder="Search by title, category or product" value={query} onChange={(e) => { setQuery(e.target.value); setPage(1); }} />
          </div>
          <label htmlFor="doc-status" className="sr-only">Status</label>
          <Select id="doc-status" className="sm:w-48" value={status} onChange={(e) => { setStatus(e.target.value as DocumentStatus | ""); setPage(1); }}>
            <option value="">Active and inactive</option>
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive</option>
            <option value="DELETED">Deleted</option>
          </Select>
        </div>

        {notice && <Alert tone={notice.tone} action={<IconButton icon="close" label="Dismiss" onClick={() => setNotice(null)} />}>{notice.text}</Alert>}
        {error && <Alert tone="red">{error}</Alert>}

        <Card className="overflow-hidden">
          {!data && <div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>}
          {data && data.items.length === 0 && (
            <EmptyState icon="document" title={q || status ? "No documents match" : "No documents yet"} description="Upload policies, FAQs and guides to let the assistant answer from them."
              action={<Button icon="upload" onClick={() => setUploadOpen(true)}>Upload document</Button>} />
          )}
          {data && data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-3 font-medium">Document</th>
                    <th scope="col" className="hidden px-4 py-3 font-medium md:table-cell">Authority</th>
                    <th scope="col" className="px-4 py-3 font-medium">Status</th>
                    <th scope="col" className="hidden px-4 py-3 font-medium lg:table-cell">Version</th>
                    <th scope="col" className="hidden px-4 py-3 font-medium lg:table-cell">Chunks</th>
                    <th scope="col" className="hidden px-4 py-3 font-medium sm:table-cell">Updated</th>
                    <th scope="col" className="px-4 py-3"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((d) => {
                    const latest = d.latest_version;
                    return (
                      <tr key={d.id} className="hover:bg-slate-50">
                        <td className="max-w-xs px-4 py-3">
                          <div className="flex items-center gap-3">
                            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-[10px] font-bold text-slate-600">{d.source_type === "MARKDOWN" ? "MD" : d.source_type}</span>
                            <div className="min-w-0">
                              <Link to={`/admin/documents/${d.id}`} className="block truncate font-medium text-slate-900 hover:text-brand-700">{d.title}</Link>
                              <span className="block truncate text-xs text-slate-500">{[d.category, d.product].filter(Boolean).join(" · ") || latest?.filename}</span>
                            </div>
                          </div>
                        </td>
                        <td className="hidden px-4 py-3 text-slate-600 md:table-cell">{humanize(d.authority)}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-col items-start gap-1">
                            <ProcessingBadge status={d.processing_status} />
                            {latest?.status === "FAILED" && <span className="max-w-[14rem] truncate text-xs text-red-600" title={latest.error_message ?? ""}>{latest.error_message}</span>}
                          </div>
                        </td>
                        <td className="hidden px-4 py-3 text-slate-600 lg:table-cell">
                          {d.active_version_number ? `v${d.active_version_number}` : "-"}
                          {latest && latest.version_number !== d.active_version_number && <span className="text-xs text-slate-400"> (v{latest.version_number} {humanize(latest.status).toLowerCase()})</span>}
                        </td>
                        <td className="hidden px-4 py-3 text-slate-600 lg:table-cell">{latest?.chunk_count || "-"}</td>
                        <td className="hidden whitespace-nowrap px-4 py-3 text-slate-600 sm:table-cell">{relativeTime(d.updated_at)}</td>
                        <td className="whitespace-nowrap px-4 py-3 text-right">
                          {d.status !== "DELETED" && (
                            <div className="flex justify-end gap-1">
                              <IconButton icon="refresh" label={`Reindex ${d.title}`} disabled={busyId === d.id || IN_PROGRESS.has(d.processing_status)}
                                onClick={() => run(d.id, () => api.reindexDocument(d.id), `Reindexing "${d.title}". The current version keeps serving answers until it finishes.`)} />
                              <IconButton icon="power" label={d.status === "ACTIVE" ? `Deactivate ${d.title}` : `Activate ${d.title}`} disabled={busyId === d.id}
                                onClick={() => run(d.id, () => api.updateDocument(d.id, { status: d.status === "ACTIVE" ? "INACTIVE" : "ACTIVE" }), d.status === "ACTIVE" ? `"${d.title}" is no longer used for answers.` : `"${d.title}" is active again.`)} />
                              <IconButton icon="trash" label={`Delete ${d.title}`} className="hover:text-red-600" disabled={busyId === d.id} onClick={() => setToDelete(d)} />
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {data && <Pagination page={data.page} pages={data.pages} total={data.total} onChange={setPage} />}
        </Card>
      </div>

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={(result) => {
          setUploadOpen(false);
          setNotice({ tone: result.duplicate ? "blue" : "green", text: result.message });
          setPage(1);
          load();
        }}
      />
      <ConfirmDialog
        open={!!toDelete}
        title="Delete document?"
        message={`"${toDelete?.title}" will be removed from the knowledge base immediately and no longer used for answers. Its version history is kept for audit.`}
        confirmLabel="Delete"
        danger
        busy={!!toDelete && busyId === toDelete.id}
        onClose={() => setToDelete(null)}
        onConfirm={() => toDelete && run(toDelete.id, async () => { await api.deleteDocument(toDelete.id); setToDelete(null); }, "Document deleted.")}
      />
    </div>
  );
}
