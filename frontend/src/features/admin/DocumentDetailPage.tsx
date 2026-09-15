import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Icon } from "../../components/icons";
import { ProcessingBadge } from "../../components/StatusBadge";
import { Alert, Badge, Button, Card, ConfirmDialog, EmptyState, Field, Input, Select, Skeleton } from "../../components/ui";
import { api, ApiError, errorMessage } from "../../lib/api";
import { dateTime, fileSize, humanize } from "../../lib/format";
import { usePolling } from "../../lib/hooks";
import type { DocumentDetail, SourceAuthority } from "../../lib/types";
import { PageHeader } from "./AdminLayout";
import { AUTHORITIES, UploadDialog } from "./UploadDialog";

function MetadataForm({ detail, onSaved }: { detail: DocumentDetail; onSaved: () => void }) {
  const d = detail.document;
  const [form, setForm] = useState({
    title: d.title, authority: d.authority, category: d.category ?? "", product: d.product ?? "",
    locale: d.locale ?? "", effective_date: d.effective_date ?? "", source_uri: d.source_uri ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof form) => (value: string) => setForm((f) => ({ ...f, [key]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.updateDocument(d.id, {
        title: form.title.trim(), authority: form.authority as SourceAuthority, category: form.category || null, product: form.product || null,
        locale: form.locale || null, effective_date: form.effective_date || null, source_uri: form.source_uri || null,
      });
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 p-4">
      {error && <Alert tone="red">{error}</Alert>}
      <Field label="Title" htmlFor="meta-title"><Input id="meta-title" required maxLength={300} value={form.title} onChange={(e) => set("title")(e.target.value)} /></Field>
      <Field label="Source authority" htmlFor="meta-authority">
        <Select id="meta-authority" value={form.authority} onChange={(e) => set("authority")(e.target.value)}>
          {AUTHORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </Select>
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Category" htmlFor="meta-category"><Input id="meta-category" value={form.category} onChange={(e) => set("category")(e.target.value)} /></Field>
        <Field label="Product" htmlFor="meta-product"><Input id="meta-product" value={form.product} onChange={(e) => set("product")(e.target.value)} /></Field>
        <Field label="Locale" htmlFor="meta-locale"><Input id="meta-locale" value={form.locale} onChange={(e) => set("locale")(e.target.value)} placeholder="en-US" /></Field>
        <Field label="Effective date" htmlFor="meta-effective"><Input id="meta-effective" type="date" value={form.effective_date} onChange={(e) => set("effective_date")(e.target.value)} /></Field>
      </div>
      <Field label="Source URL" htmlFor="meta-uri"><Input id="meta-uri" type="url" value={form.source_uri} onChange={(e) => set("source_uri")(e.target.value)} /></Field>
      <div className="flex justify-end"><Button type="submit" loading={busy}>Save changes</Button></div>
    </form>
  );
}

export default function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(() => {
    api.document(documentId)
      .then((result) => { setDetail(result); setError(null); })
      .catch((err) => setError(err instanceof ApiError ? err : new ApiError(0, "UNKNOWN", errorMessage(err))));
  }, [documentId]);
  useEffect(load, [load]);
  usePolling(load, 3000, !!detail?.versions.some((v) => v.status === "UPLOADED" || v.status === "PROCESSING"));

  async function act(name: string, action: () => Promise<unknown>, success?: string) {
    setBusy(name);
    setActionError(null);
    try {
      await action();
      if (success) setNotice(success);
      load();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return error.status === 404
      ? <EmptyState icon="document" title="Document not found" action={<Link to="/admin/documents" className="text-sm font-medium text-brand-700">Back to knowledge base</Link>} />
      : <div className="p-8"><Alert tone="red" title="Couldn't load the document">{error.message}</Alert></div>;
  }
  if (!detail) return <div className="space-y-3 p-8"><Skeleton className="h-8 w-80" /><Skeleton className="h-64 w-full" /></div>;

  const d = detail.document;
  const deleted = d.status === "DELETED";

  return (
    <div>
      <PageHeader
        title={d.title}
        description={`${d.source_type} · ${humanize(d.authority)}${d.searchable ? " · used for answers" : " · not used for answers"}`}
        badge={<ProcessingBadge status={d.processing_status} />}
        actions={
          <>
            <Link to="/admin/documents" className="inline-flex h-8 items-center gap-1 rounded-lg px-3 text-sm text-slate-600 hover:bg-slate-100"><Icon name="arrowLeft" size={16} /> Back</Link>
            {!deleted && (
              <>
                <Button size="sm" variant="secondary" icon="upload" onClick={() => setUploadOpen(true)}>New version</Button>
                <Button size="sm" variant="secondary" icon="refresh" loading={busy === "reindex"} onClick={() => act("reindex", () => api.reindexDocument(d.id), "Reindex queued.")}>Reindex</Button>
                <Button size="sm" variant="secondary" icon="power" loading={busy === "toggle"}
                  onClick={() => act("toggle", () => api.updateDocument(d.id, { status: d.status === "ACTIVE" ? "INACTIVE" : "ACTIVE" }))}>
                  {d.status === "ACTIVE" ? "Deactivate" : "Activate"}
                </Button>
                <Button size="sm" variant="danger" icon="trash" onClick={() => setConfirmDelete(true)}>Delete</Button>
              </>
            )}
          </>
        }
      />
      <div className="grid gap-6 px-4 py-6 sm:px-8 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="space-y-4">
          {notice && <Alert tone="green">{notice}</Alert>}
          {actionError && <Alert tone="red">{actionError}</Alert>}
          <Card>
            <h2 className="px-4 pt-4 text-sm font-semibold text-slate-900">Metadata</h2>
            {deleted ? <p className="p-4 text-sm text-slate-500">Deleted documents are read-only.</p> : <MetadataForm key={d.updated_at} detail={detail} onSaved={() => { setNotice("Metadata saved."); load(); }} />}
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="overflow-hidden">
            <h2 className="px-4 pt-4 text-sm font-semibold text-slate-900">Versions</h2>
            <ul className="mt-2 divide-y divide-slate-100">
              {detail.versions.map((v) => (
                <li key={v.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-slate-900">v{v.version_number}</span>
                    <ProcessingBadge status={v.status} />
                    {d.active_version_number === v.version_number && <Badge tone="green">Live</Badge>}
                    <span className="truncate text-xs text-slate-500">{v.filename} · {fileSize(v.size_bytes)}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    Uploaded {dateTime(v.created_at)}{v.processed_at ? ` · processed ${dateTime(v.processed_at)}` : ""} · {v.chunk_count} chunks
                    {v.page_count ? ` · ${v.page_count} pages` : ""}{v.embedding_model ? ` · ${v.embedding_model}` : ""}{v.effective_date ? ` · effective ${v.effective_date}` : ""}
                  </p>
                  {v.status === "FAILED" && (
                    <p className="mt-1.5 rounded-md bg-red-50 px-2 py-1.5 text-xs text-red-700" role="alert">
                      <span className="font-semibold">{humanize(v.error_code)}:</span> {v.error_message} (attempts: {v.attempts})
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="overflow-hidden">
            <h2 className="px-4 pt-4 text-sm font-semibold text-slate-900">Indexed chunks {detail.chunks.length > 0 && <span className="font-normal text-slate-500">(live version, first {detail.chunks.length})</span>}</h2>
            {detail.chunks.length === 0 ? (
              <p className="p-4 text-sm text-slate-500">No live version is indexed yet.</p>
            ) : (
              <ul className="mt-2 divide-y divide-slate-100">
                {detail.chunks.map((c) => {
                  const flags = (c.metadata.injection_flags as string[] | undefined) ?? [];
                  const open = expanded === c.chunk_index;
                  return (
                    <li key={c.chunk_index} className="px-4 py-3">
                      <button type="button" className="flex w-full items-start gap-2 text-left" aria-expanded={open} onClick={() => setExpanded(open ? null : c.chunk_index)}>
                        <span className="mt-0.5 font-mono text-xs text-slate-400">#{c.chunk_index}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-slate-800">{c.heading_path ?? "(no section)"}</span>
                          <span className="block text-xs text-slate-500">{c.page_number ? `Page ${c.page_number} · ` : ""}{c.token_count} tokens</span>
                        </span>
                        {flags.length > 0 && <Badge tone="red">Instruction-like text</Badge>}
                        <Icon name="chevronDown" size={16} className={`mt-0.5 text-slate-400 ${open ? "rotate-180" : ""}`} />
                      </button>
                      {open && <p className="mt-2 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">{c.content}</p>}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <UploadDialog open={uploadOpen} documentId={d.id} documentTitle={d.title} onClose={() => setUploadOpen(false)}
        onUploaded={(result) => { setUploadOpen(false); setNotice(result.message); load(); }} />
      <ConfirmDialog open={confirmDelete} title="Delete document?" danger confirmLabel="Delete" busy={busy === "delete"}
        message="The document stops being used for answers immediately. Version history is kept for audit."
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => act("delete", async () => { await api.deleteDocument(d.id); navigate("/admin/documents"); })} />
    </div>
  );
}
