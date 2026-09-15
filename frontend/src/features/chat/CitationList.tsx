import { useId, useState } from "react";
import { Icon } from "../../components/icons";
import { humanize } from "../../lib/format";
import type { Citation } from "../../lib/types";

function location(citation: Citation): string {
  const parts: string[] = [];
  if (citation.page_number) parts.push(`Page ${citation.page_number}`);
  if (citation.section_title) parts.push(citation.section_title);
  return parts.join(" · ");
}

function safeLink(uri: string | null): string | null {
  if (!uri) return null;
  try {
    const url = new URL(uri);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function CitationItem({ citation, index }: { citation: Citation; index: number }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const link = safeLink(citation.source_uri);
  return (
    <li className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        className="flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-slate-50"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-slate-100 text-[11px] font-semibold text-slate-600">
          {index + 1}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-slate-800">{citation.document_title}</span>
          {location(citation) && <span className="block truncate text-xs text-slate-500">{location(citation)}</span>}
        </span>
        <Icon name="chevronDown" size={16} className={`mt-1 shrink-0 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div id={panelId} className="border-t border-slate-100 px-3 pb-3 pt-2">
          <blockquote className="border-l-2 border-brand-200 pl-3 text-sm leading-relaxed text-slate-700">"{citation.excerpt}"</blockquote>
          <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
            <span>{humanize(citation.authority)}</span>
            <span>Version {citation.version}</span>
            {citation.effective_date && <span>Effective {citation.effective_date}</span>}
            {link && (
              <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-brand-700 hover:underline">
                Open source <Icon name="external" size={12} />
              </a>
            )}
          </p>
        </div>
      )}
    </li>
  );
}

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <Icon name="book" size={14} /> Sources
      </p>
      <ol className="space-y-1.5">
        {citations.map((citation, index) => (
          <CitationItem key={`${citation.chunk_id}-${index}`} citation={citation} index={index} />
        ))}
      </ol>
    </div>
  );
}
