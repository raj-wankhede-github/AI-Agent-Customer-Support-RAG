import type { ReactNode } from "react";
import { Badge } from "../../components/ui";
import { humanize } from "../../lib/format";
import type { Json, Trace } from "../../lib/types";

const num = (value: unknown, digits = 3) => (typeof value === "number" ? value.toFixed(digits) : "-");

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-slate-100 px-4 py-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </div>
  );
}

function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-0.5 text-sm">
      <dt className="text-slate-500">{label}</dt>
      <dd className="min-w-0 text-right text-slate-800">{children}</dd>
    </div>
  );
}

export function CandidateTable({ candidates, selectedIds }: { candidates: Json[]; selectedIds: Set<string> }) {
  if (candidates.length === 0) return <p className="text-sm text-slate-500">No candidates were retrieved.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-xs">
        <thead className="text-slate-500">
          <tr>
            <th scope="col" className="py-1 pr-2 font-medium">Source</th>
            <th scope="col" className="px-1 py-1 text-right font-medium" title="Reranker score">Rerank</th>
            <th scope="col" className="px-1 py-1 text-right font-medium" title="Query term coverage">Cover</th>
            <th scope="col" className="px-1 py-1 text-right font-medium" title="Vector cosine similarity">Vector</th>
            <th scope="col" className="px-1 py-1 text-right font-medium" title="Full-text rank position">FTS #</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {candidates.map((c) => {
            const id = String(c.chunk_id);
            const selected = selectedIds.has(id);
            return (
              <tr key={id} className={selected ? "bg-emerald-50/60" : ""}>
                <td className="max-w-[14rem] py-1.5 pr-2">
                  <span className="block truncate font-medium text-slate-800" title={String(c.excerpt ?? "")}>
                    {selected && <span className="sr-only">Selected: </span>}
                    {String(c.document_title)}
                  </span>
                  <span className="block truncate text-slate-500">{String(c.section_title ?? "")}{c.page_number ? ` · p.${c.page_number}` : ""}</span>
                </td>
                <td className="px-1 py-1.5 text-right font-mono">{num(c.rerank_score)}</td>
                <td className="px-1 py-1.5 text-right font-mono">{num(c.coverage, 2)}</td>
                <td className="px-1 py-1.5 text-right font-mono">{num(c.vector_similarity)}</td>
                <td className="px-1 py-1.5 text-right font-mono">{c.lexical_rank ? String(c.lexical_rank) : "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function TraceInspector({ trace }: { trace: Trace }) {
  const analysis = trace.analysis as Json;
  const sufficiency = trace.sufficiency as Json;
  const validation = trace.validation as Json;
  const confidence = trace.confidence as Json;
  const selectedIds = new Set(trace.selected_evidence.map((e) => String(e.chunk_id)));
  const issues = (validation.issues as Json[] | undefined) ?? [];
  const flags = (analysis.injection_flags as string[] | undefined) ?? [];
  const keyTerms = (analysis.key_terms as string[] | undefined) ?? [];

  return (
    <div className="text-sm">
      <div className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={trace.decision === "ANSWERED" ? "green" : trace.decision === "ABSTAINED" ? "amber" : "violet"}>{humanize(trace.decision)}</Badge>
          {trace.handoff_reason && <Badge tone="blue">{humanize(trace.handoff_reason)}</Badge>}
          {typeof confidence.level === "string" && <Badge>Confidence: {humanize(confidence.level)}</Badge>}
        </div>
        {trace.decision_reason && <p className="mt-2 text-slate-700">{trace.decision_reason}</p>}
      </div>

      <Section title="Query understanding">
        <dl>
          <KV label="Customer asked">{trace.original_query}</KV>
          <KV label="Standalone query">{trace.standalone_query}</KV>
          <KV label="Intent">{humanize(String(analysis.intent ?? "-"))}</KV>
          <KV label="Sensitive">{humanize(String(analysis.sensitive_category ?? "none"))}</KV>
          <KV label="Analyzer">{String(analysis.analyzer ?? "-")}</KV>
        </dl>
        {keyTerms.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">{keyTerms.map((t) => <Badge key={t}>{t}</Badge>)}</div>
        )}
        {flags.length > 0 && <p className="mt-2 text-xs text-red-700">Injection patterns: {flags.join(", ")}</p>}
      </Section>

      {Object.keys(sufficiency).length > 0 && (
        <Section title="Evidence sufficiency">
          <dl>
            <KV label="Verdict">{sufficiency.sufficient ? "Sufficient" : `Insufficient (${humanize(String(sufficiency.reason_code))})`}</KV>
            <KV label="Top relevance">{num(sufficiency.top_score)}</KV>
            <KV label="Coverage">{num(sufficiency.coverage, 2)}</KV>
            {Array.isArray(sufficiency.missing_terms) && sufficiency.missing_terms.length > 0 && (
              <KV label="Not found">{(sufficiency.missing_terms as string[]).join(", ")}</KV>
            )}
          </dl>
        </Section>
      )}

      {trace.candidates.length > 0 && (
        <Section title={`Retrieved chunks (${trace.candidates.length})`}>
          <CandidateTable candidates={trace.candidates} selectedIds={selectedIds} />
          <p className="mt-1 text-[11px] text-slate-400">Highlighted rows were selected as evidence.</p>
        </Section>
      )}

      {trace.conflicts.length > 0 && (
        <Section title="Conflicts">
          <ul className="space-y-2">
            {trace.conflicts.map((c, i) => (
              <li key={i} className="rounded-md bg-slate-50 p-2 text-xs">
                <p className="font-medium text-slate-800">{c.resolved ? `Resolved by ${humanize(String(c.resolution))}` : "Unresolved"}</p>
                {Object.entries((c.values as Record<string, string>) ?? {}).map(([doc, value]) => (
                  <p key={doc} className="text-slate-600">{doc}: <span className="font-mono">{value}</span></p>
                ))}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {Object.keys(validation).length > 0 && (
        <Section title="Grounding validation">
          <dl>
            <KV label="Result">{validation.passed ? "Passed" : "Failed"}</KV>
            <KV label="Min claim support">{num(validation.claim_support, 2)}</KV>
            <KV label="Attempt">{String(validation.attempt ?? 1)}</KV>
          </dl>
          {issues.length > 0 && (
            <ul className="mt-2 space-y-1">
              {issues.map((issue, i) => (
                <li key={i} className="text-xs text-red-700">{humanize(String(issue.kind))}: {String(issue.detail)}</li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {confidence.components ? (
        <Section title="Confidence signals">
          <dl>
            <KV label="Score">{num(confidence.score, 2)}</KV>
            {Object.entries(confidence.components as Record<string, number>).map(([k, v]) => <KV key={k} label={humanize(k)}>{num(v, 2)}</KV>)}
          </dl>
        </Section>
      ) : null}

      <Section title="Execution">
        <dl>
          <KV label="Provider / model">{trace.provider ?? "-"} / {trace.model ?? "-"}</KV>
          <KV label="Embedding model">{trace.embedding_model ?? "-"}</KV>
          <KV label="Tokens in / out">{trace.input_tokens} / {trace.output_tokens}</KV>
          {Object.entries(trace.timings_ms).map(([k, v]) => <KV key={k} label={`${humanize(k)} (ms)`}>{v}</KV>)}
          {Object.entries(trace.prompt_versions).map(([k, v]) => <KV key={k} label={`Prompt: ${k}`}><span className="font-mono text-xs">{v}</span></KV>)}
        </dl>
      </Section>
    </div>
  );
}
