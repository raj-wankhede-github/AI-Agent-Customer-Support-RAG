import { useState } from "react";
import { Icon } from "../../components/icons";
import { Button, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import type { Feedback, FeedbackRating, FeedbackReason } from "../../lib/types";

const REASONS: [FeedbackReason, string][] = [
  ["INCORRECT", "Incorrect"],
  ["NOT_RELEVANT", "Not relevant"],
  ["MISSING_INFORMATION", "Missing information"],
  ["TOO_VERBOSE", "Too verbose"],
  ["OTHER", "Other"],
];

export function FeedbackControls({
  feedback,
  onRate,
}: {
  feedback: Feedback | null;
  onRate: (rating: FeedbackRating, reason?: FeedbackReason | null, comment?: string | null) => Promise<void>;
}) {
  const [askingReason, setAskingReason] = useState(false);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(rating: FeedbackRating, withReason?: FeedbackReason | null, withComment?: string) {
    setSaving(true);
    setError(null);
    try {
      await onRate(rating, withReason, withComment || null);
      setAskingReason(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const rated = feedback?.rating;
  return (
    <div className="mt-2">
      <div className="flex items-center gap-1 text-slate-500">
        <span className="mr-1 text-xs">{rated ? "Thanks for your feedback" : "Was this helpful?"}</span>
        <button
          type="button"
          aria-label="Helpful"
          aria-pressed={rated === "HELPFUL"}
          disabled={saving}
          onClick={() => submit("HELPFUL")}
          className={`rounded-md p-1.5 hover:bg-slate-100 ${rated === "HELPFUL" ? "text-emerald-600" : ""}`}
        >
          <Icon name="thumbUp" size={16} />
        </button>
        <button
          type="button"
          aria-label="Not helpful"
          aria-pressed={rated === "NOT_HELPFUL"}
          disabled={saving}
          onClick={() => {
            setAskingReason(true);
            setReason(null);
          }}
          className={`rounded-md p-1.5 hover:bg-slate-100 ${rated === "NOT_HELPFUL" ? "text-red-600" : ""}`}
        >
          <Icon name="thumbDown" size={16} />
        </button>
      </div>
      {askingReason && (
        <fieldset className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <legend className="sr-only">What went wrong?</legend>
          <p className="mb-2 text-xs font-medium text-slate-600">What went wrong? (optional)</p>
          <div className="flex flex-wrap gap-1.5">
            {REASONS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={reason === value}
                onClick={() => setReason(reason === value ? null : value)}
                className={`rounded-full border px-2.5 py-1 text-xs ${reason === value ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="sr-only" htmlFor="feedback-comment">Comment</label>
          <Input id="feedback-comment" className="mt-2" placeholder="Tell us more (optional)" maxLength={1000} value={comment} onChange={(e) => setComment(e.target.value)} />
          {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
          <div className="mt-2 flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setAskingReason(false)}>Cancel</Button>
            <Button size="sm" loading={saving} onClick={() => submit("NOT_HELPFUL", reason, comment)}>Send feedback</Button>
          </div>
        </fieldset>
      )}
      {!askingReason && error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}
