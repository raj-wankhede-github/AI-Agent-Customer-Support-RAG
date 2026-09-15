import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Icon } from "../../components/icons";
import { Alert, Button, Field, Input, Modal, Select } from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { fileSize } from "../../lib/format";
import type { SourceAuthority, UploadResponse } from "../../lib/types";

const ACCEPT = [".pdf", ".txt", ".md", ".markdown", ".html", ".htm", ".docx"];
const MAX_BYTES = 20 * 1024 * 1024;
export const AUTHORITIES: [SourceAuthority, string][] = [
  ["OFFICIAL_POLICY", "Official policy"],
  ["OFFICIAL_DOCUMENTATION", "Official documentation"],
  ["PRODUCT_DOCUMENTATION", "Product documentation"],
  ["FAQ", "FAQ"],
  ["SUPPORT_ARTICLE", "Support article"],
  ["INTERNAL_GUIDE", "Internal guide"],
  ["LOW_PRIORITY", "Low priority"],
];

function validateFile(file: File): string | null {
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPT.includes(extension)) return `Unsupported file type. Allowed: ${ACCEPT.join(", ")}`;
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_BYTES) return `The file is ${fileSize(file.size)}; the limit is 20 MB.`;
  return null;
}

export function UploadDialog({ open, onClose, onUploaded, documentId, documentTitle }: {
  open: boolean;
  onClose: () => void;
  onUploaded: (result: UploadResponse) => void;
  documentId?: string;
  documentTitle?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [title, setTitle] = useState("");
  const [authority, setAuthority] = useState<SourceAuthority>("SUPPORT_ARTICLE");
  const [category, setCategory] = useState("");
  const [product, setProduct] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [sourceUri, setSourceUri] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const newVersion = !!documentId;

  function choose(selected: File | undefined) {
    if (!selected) return;
    const problem = validateFile(selected);
    setFileError(problem);
    setFile(problem ? null : selected);
  }

  function reset() {
    setFile(null);
    setFileError(null);
    setTitle("");
    setCategory("");
    setProduct("");
    setEffectiveDate("");
    setSourceUri("");
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.set("file", file);
    if (newVersion) form.set("document_id", documentId);
    else {
      if (title.trim()) form.set("title", title.trim());
      form.set("authority", authority);
      if (category.trim()) form.set("category", category.trim());
      if (product.trim()) form.set("product", product.trim());
      if (sourceUri.trim()) form.set("source_uri", sourceUri.trim());
    }
    if (effectiveDate) form.set("effective_date", effectiveDate);
    setBusy(true);
    setError(null);
    try {
      const result = await api.uploadDocument(form);
      reset();
      onUploaded(result);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  return (
    <Modal
      open={open}
      title={newVersion ? `Upload a new version of "${documentTitle}"` : "Upload a document"}
      onClose={() => { reset(); onClose(); }}
      footer={
        <>
          <Button variant="secondary" onClick={() => { reset(); onClose(); }} disabled={busy}>Cancel</Button>
          <Button type="submit" form="upload-form" icon="upload" loading={busy} disabled={!file}>Upload</Button>
        </>
      }
    >
      <form id="upload-form" onSubmit={submit} className="space-y-4">
        {error && <Alert tone="red">{error}</Alert>}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-6 text-center ${dragging ? "border-brand-500 bg-brand-50" : "border-slate-300"}`}
        >
          <span className="mb-2 text-slate-400"><Icon name="attachment" size={26} /></span>
          {file ? (
            <p className="text-sm text-slate-800"><span className="font-medium">{file.name}</span> <span className="text-slate-500">({fileSize(file.size)})</span></p>
          ) : (
            <p className="text-sm text-slate-600">Drag a file here, or</p>
          )}
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => input.current?.click()}>{file ? "Choose another file" : "Browse files"}</Button>
          <input ref={input} type="file" accept={ACCEPT.join(",")} className="sr-only" aria-label="Document file" onChange={(e) => choose(e.target.files?.[0])} />
          <p className="mt-2 text-xs text-slate-500">PDF, DOCX, HTML, Markdown or TXT · up to 20 MB</p>
          {fileError && <p className="mt-2 text-xs text-red-600" role="alert">{fileError}</p>}
        </div>

        {!newVersion && (
          <>
            <Field label="Title" htmlFor="upload-title" hint="Defaults to the file name">
              <Input id="upload-title" maxLength={300} value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Field label="Source authority" htmlFor="upload-authority" hint="Used to resolve conflicts between documents">
              <Select id="upload-authority" value={authority} onChange={(e) => setAuthority(e.target.value as SourceAuthority)}>
                {AUTHORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </Select>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Category" htmlFor="upload-category"><Input id="upload-category" maxLength={100} value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. shipping" /></Field>
              <Field label="Product" htmlFor="upload-product"><Input id="upload-product" maxLength={100} value={product} onChange={(e) => setProduct(e.target.value)} placeholder="Optional" /></Field>
            </div>
            <Field label="Source URL" htmlFor="upload-uri"><Input id="upload-uri" type="url" maxLength={1000} value={sourceUri} onChange={(e) => setSourceUri(e.target.value)} placeholder="https://" /></Field>
          </>
        )}
        <Field label="Effective date" htmlFor="upload-effective" hint="When this content takes effect. Detected from the text if left empty.">
          <Input id="upload-effective" type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}
