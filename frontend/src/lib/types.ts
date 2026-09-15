// Mirrors backend/app/schemas. Keep in sync with the OpenAPI document at /openapi.json.

export type UserRole = "ADMIN" | "AGENT" | "CUSTOMER";
export type ConversationStatus = "OPEN" | "WAITING_FOR_CUSTOMER" | "WAITING_FOR_HUMAN" | "RESOLVED" | "CLOSED";
export type HandoffStatus = "NONE" | "PENDING" | "ASSIGNED" | "RESOLVED";
export type HandoffPriority = "LOW" | "NORMAL" | "HIGH" | "URGENT";
export type MessageRole = "USER" | "ASSISTANT" | "SYSTEM" | "HUMAN_AGENT";
export type AnswerStatus = "ANSWERED" | "ABSTAINED" | "HANDOFF_REQUIRED" | "AWAITING_HUMAN";
export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW" | "ABSTAIN";
export type FeedbackRating = "HELPFUL" | "NOT_HELPFUL";
export type FeedbackReason = "INCORRECT" | "NOT_RELEVANT" | "MISSING_INFORMATION" | "TOO_VERBOSE" | "OTHER";
export type DocumentStatus = "ACTIVE" | "INACTIVE" | "DELETED";
export type VersionStatus = "UPLOADED" | "PROCESSING" | "READY" | "FAILED" | "SUPERSEDED";
export type SourceType = "PDF" | "TXT" | "MARKDOWN" | "HTML" | "DOCX";
export type SourceAuthority =
  | "OFFICIAL_POLICY"
  | "OFFICIAL_DOCUMENTATION"
  | "PRODUCT_DOCUMENTATION"
  | "FAQ"
  | "SUPPORT_ARTICLE"
  | "INTERNAL_GUIDE"
  | "LOW_PRIORITY";

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  company_id: string;
  company_name: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface Conversation {
  id: string;
  title: string;
  status: ConversationStatus;
  handoff_status: HandoffStatus;
  last_message_preview: string | null;
  last_message_at: string;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  closed_by: ActorRef | null;
  closed_automatically: boolean;
  resolved_at: string | null;
  resolved_by: ActorRef | null;
  reopened_at: string | null;
  reopen_count: number;
}

export interface ActorRef {
  id: string;
  name: string;
  role: UserRole;
}

export interface Citation {
  evidence_id: string;
  chunk_id: string;
  document_id: string;
  document_version_id: string;
  document_title: string;
  version: number;
  source_type: string;
  source_uri: string | null;
  authority: string;
  section_title: string | null;
  heading_path: string | null;
  page_number: number | null;
  chunk_index: number;
  effective_date: string | null;
  excerpt: string;
}

export interface Feedback {
  rating: FeedbackRating;
  reason: string | null;
  comment: string | null;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  answer_status: AnswerStatus | null;
  response_kind: string | null;
  confidence: ConfidenceLevel | null;
  citations: Citation[];
  handoff_reason: string | null;
  author_name: string | null;
  feedback: Feedback | null;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
}

export interface ChatResponse {
  conversation_id: string;
  user_message_id: string;
  message_id: string | null;
  answer: string | null;
  citations: Citation[];
  answer_status: AnswerStatus;
  confidence: ConfidenceLevel | null;
  handoff_required: boolean;
  handoff_reason: string | null;
  conversation_status: ConversationStatus;
  message: Message | null;
}

export interface UserRef {
  id: string;
  name: string;
  email: string;
  role: UserRole;
}

export interface Handoff {
  id: string;
  conversation_id: string;
  conversation_title: string;
  customer: UserRef;
  reason_code: string;
  reason_detail: string;
  priority: HandoffPriority;
  status: HandoffStatus;
  assigned_agent: UserRef | null;
  triggering_message_id: string | null;
  triggering_message_preview: string | null;
  created_at: string;
  assigned_at: string | null;
  resolved_at: string | null;
  waiting_seconds: number;
}

export interface AdminConversation extends Conversation {
  customer: UserRef;
  assigned_agent: UserRef | null;
  message_count: number;
}

export type Json = Record<string, unknown>;

export interface Trace {
  id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  original_query: string;
  standalone_query: string;
  decision: string;
  decision_reason: string | null;
  handoff_reason: string | null;
  analysis: Json;
  candidates: Json[];
  selected_evidence: Json[];
  sufficiency: Json;
  conflicts: Json[];
  validation: Json;
  confidence: Json;
  timings_ms: Record<string, number>;
  prompt_versions: Record<string, string>;
  provider: string | null;
  model: string | null;
  embedding_model: string | null;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface AdminConversationDetail {
  conversation: AdminConversation;
  messages: Message[];
  handoffs: Handoff[];
  traces: Trace[];
}

export interface Version {
  id: string;
  version_number: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  checksum: string;
  status: VersionStatus;
  error_code: string | null;
  error_message: string | null;
  attempts: number;
  embedding_model: string | null;
  chunk_count: number;
  page_count: number | null;
  extracted_title: string | null;
  effective_date: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source_type: SourceType;
  source_uri: string | null;
  authority: SourceAuthority;
  category: string | null;
  product: string | null;
  locale: string | null;
  effective_date: string | null;
  status: DocumentStatus;
  processing_status: string;
  searchable: boolean;
  active_version_number: number | null;
  latest_version: Version | null;
  created_at: string;
  updated_at: string;
}

export interface ChunkPreview {
  chunk_index: number;
  section_title: string | null;
  heading_path: string | null;
  page_number: number | null;
  token_count: number;
  content: string;
  metadata: Json;
}

export interface DocumentDetail {
  document: KnowledgeDocument;
  versions: Version[];
  chunks: ChunkPreview[];
}

export interface UploadResponse {
  document: KnowledgeDocument;
  version: Version;
  duplicate: boolean;
  message: string;
}

export interface CountItem {
  key: string;
  count: number;
}

export interface Metrics {
  window_days: number;
  conversations_total: number;
  conversations_open: number;
  conversations_waiting_for_human: number;
  conversations_resolved: number;
  conversations_closed: number;
  assistant_responses: number;
  answered: number;
  abstained: number;
  handoff_responses: number;
  abstention_rate: number;
  handoff_rate: number;
  pending_handoffs: number;
  avg_response_latency_ms: number | null;
  p95_response_latency_ms: number | null;
  feedback_helpful: number;
  feedback_not_helpful: number;
  feedback_reasons: CountItem[];
  documents_active: number;
  documents_inactive: number;
  documents_searchable: number;
  versions_by_status: CountItem[];
  ingestion_failures: number;
  retrieval_success_rate: number | null;
  citation_validation_failures: number;
  low_confidence_responses: number;
  handoff_reasons: CountItem[];
  abstention_reasons: CountItem[];
  recent_unanswered: {
    trace_id: string;
    conversation_id: string;
    question: string;
    decision: string;
    reason: string | null;
    created_at: string;
  }[];
  input_tokens: number;
  output_tokens: number;
}

export interface RetrievalDebug {
  query: string;
  standalone_query: string;
  key_terms: string[];
  lexical_query: string;
  embedding_model: string;
  candidates: Json[];
  selected_evidence: Json[];
  sufficiency: Json;
  conflicts: Json[];
}
