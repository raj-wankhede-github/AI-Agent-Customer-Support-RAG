import { useState, type FormEvent } from "react";
import { Alert, Badge, Button, Card, Input } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { humanize } from "../../lib/format";
import type { RetrievalDebug } from "../../lib/types";
import { PageHeader } from "./AdminLayout";
import { CandidateTable } from "./TraceInspector";

export default function RetrievalDebugPage() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<RetrievalDebug | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.retrievalDebug(query.trim()));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const sufficiency = result?.sufficiency ?? {};
  const selectedIds = new Set((result?.selected_evidence ?? []).map((e) => String(e.chunk_id)));

  return (
    <div>
      <PageHeader title="Retrieval debugger" description="See what the knowledge base returns for a question and whether it would be enough to answer. Nothing is generated or saved." />
      <div className="space-y-4 px-4 py-6 sm:px-8">
        <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="debug-query" className="sr-only">Question</label>
          <Input id="debug-query" value={query} maxLength={1000} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. How long does international shipping take?" />
          <Button type="submit" icon="search" loading={busy} disabled={!query.trim()}>Inspect</Button>
        </form>
        {error && <Alert tone="red">{error}</Alert>}
        {result && (
          <>
            <div className="grid gap-4 md:grid-cols-2">
              <Card className="p-4">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Normalized query</h2>
                <p className="mt-1 text-sm text-slate-900">{result.standalone_query}</p>
                <div className="mt-3 flex flex-wrap gap-1">{result.key_terms.map((t) => <Badge key={t}>{t}</Badge>)}</div>
                <p className="mt-3 text-xs text-slate-500">Full-text query: <code className="font-mono">{result.lexical_query || "(none)"}</code></p>
                <p className="text-xs text-slate-500">Embedding model: <code className="font-mono">{result.embedding_model}</code></p>
              </Card>
              <Card className="p-4">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Evidence sufficiency</h2>
                <div className="mt-2">
                  {sufficiency.sufficient ? <Badge tone="green" dot>Sufficient to answer</Badge> : <Badge tone="amber" dot>Would abstain: {humanize(String(sufficiency.reason_code))}</Badge>}
                </div>
                <p className="mt-2 text-sm text-slate-700">{String(sufficiency.detail ?? "")}</p>
                {result.conflicts.length > 0 && (
                  <p className="mt-2 text-sm text-amber-800">
                    {result.conflicts.length} potential conflict(s): {result.conflicts.map((c) => (c.resolved ? `resolved by ${humanize(String(c.resolution))}` : "unresolved")).join(", ")}
                  </p>
                )}
              </Card>
            </div>
            <Card className="p-4">
              <h2 className="mb-2 text-sm font-semibold text-slate-900">Candidates ({result.candidates.length})</h2>
              <CandidateTable candidates={result.candidates} selectedIds={selectedIds} />
              <p className="mt-1 text-[11px] text-slate-400">Highlighted rows would be passed to the answer generator.</p>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
