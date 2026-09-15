const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function relativeTime(iso: string, now = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 7) return rtf.format(Math.round(seconds / 86400), "day");
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

type Actor = { id: string; name: string } | null;

function byWhomAndWhen(verb: string, actor: Actor, at: string | null, currentUserId?: string): string {
  const when = at ? ` on ${dateTime(at)}` : "";
  if (!actor) return `${verb}${when}`;
  return `${verb} by ${actor.id === currentUserId ? "you" : actor.name}${when}`;
}

/** "Closed by you on Sep 15, 2026, 5:42 PM" / "Closed by Sam (Support) on ..." / "Closed automatically on ..." */
export function closedLabel(
  conversation: { closed_at: string | null; closed_by: Actor; closed_automatically?: boolean },
  currentUserId?: string,
): string {
  if (conversation.closed_automatically) return byWhomAndWhen("Closed automatically", null, conversation.closed_at);
  return byWhomAndWhen("Closed", conversation.closed_by, conversation.closed_at, currentUserId);
}

/** "Resolved by Sam (Support) on Sep 15, 2026, 5:42 PM" */
export function resolvedLabel(conversation: { resolved_at: string | null; resolved_by: Actor }, currentUserId?: string): string {
  return byWhomAndWhen("Resolved", conversation.resolved_by, conversation.resolved_at, currentUserId);
}

export function humanize(code: string | null | undefined): string {
  if (!code) return "-";
  const lower = code.replace(/_/g, " ").toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
