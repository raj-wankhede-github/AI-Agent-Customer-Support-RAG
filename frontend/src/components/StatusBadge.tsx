import type { AnswerStatus, ConversationStatus, HandoffPriority, HandoffStatus } from "../lib/types";
import { humanize } from "../lib/format";
import { Badge, type Tone } from "./ui";

const CONVERSATION: Record<ConversationStatus, [string, Tone]> = {
  OPEN: ["Open", "blue"],
  WAITING_FOR_CUSTOMER: ["Awaiting your reply", "violet"],
  WAITING_FOR_HUMAN: ["Waiting for support", "amber"],
  RESOLVED: ["Resolved", "green"],
  CLOSED: ["Closed", "gray"],
};

export function ConversationStatusBadge({ status, staff = false }: { status: ConversationStatus; staff?: boolean }) {
  const [label, tone] = CONVERSATION[status];
  return <Badge tone={tone} dot>{staff && status === "WAITING_FOR_CUSTOMER" ? "Waiting for customer" : label}</Badge>;
}

const HANDOFF: Record<HandoffStatus, Tone> = { NONE: "gray", PENDING: "amber", ASSIGNED: "blue", RESOLVED: "green" };
export function HandoffStatusBadge({ status }: { status: HandoffStatus }) {
  if (status === "NONE") return null;
  return <Badge tone={HANDOFF[status]}>{humanize(status)}</Badge>;
}

const PRIORITY: Record<HandoffPriority, Tone> = { LOW: "gray", NORMAL: "blue", HIGH: "amber", URGENT: "red" };
export function PriorityBadge({ priority }: { priority: HandoffPriority }) {
  return <Badge tone={PRIORITY[priority]} dot>{humanize(priority)}</Badge>;
}

const PROCESSING: Record<string, Tone> = {
  UPLOADED: "gray", PROCESSING: "blue", READY: "green", FAILED: "red", SUPERSEDED: "gray", INACTIVE: "amber", DELETED: "gray",
};
export function ProcessingBadge({ status }: { status: string }) {
  return <Badge tone={PROCESSING[status] ?? "gray"} dot>{humanize(status)}</Badge>;
}

const ANSWER: Record<AnswerStatus, [string, Tone]> = {
  ANSWERED: ["Answered", "green"],
  ABSTAINED: ["Abstained", "amber"],
  HANDOFF_REQUIRED: ["Handoff", "violet"],
  AWAITING_HUMAN: ["Awaiting human", "blue"],
};
export function AnswerStatusBadge({ status }: { status: AnswerStatus | null }) {
  if (!status) return null;
  const [label, tone] = ANSWER[status];
  return <Badge tone={tone}>{label}</Badge>;
}
