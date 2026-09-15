from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"  # manages the knowledge base, sees all conversations, handoffs, metrics
    AGENT = "AGENT"  # human support agent: handoff queue and conversations, no KB management
    CUSTOMER = "CUSTOMER"


class ConversationStatus(StrEnum):
    OPEN = "OPEN"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class HandoffStatus(StrEnum):
    NONE = "NONE"
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"


class HandoffPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"
    HUMAN_AGENT = "HUMAN_AGENT"


class AnswerStatus(StrEnum):
    ANSWERED = "ANSWERED"
    ABSTAINED = "ABSTAINED"
    HANDOFF_REQUIRED = "HANDOFF_REQUIRED"
    AWAITING_HUMAN = "AWAITING_HUMAN"  # a human owns the conversation; the AI stays silent


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    ABSTAIN = "ABSTAIN"


class FeedbackRating(StrEnum):
    HELPFUL = "HELPFUL"
    NOT_HELPFUL = "NOT_HELPFUL"


class FeedbackReason(StrEnum):
    INCORRECT = "INCORRECT"
    NOT_RELEVANT = "NOT_RELEVANT"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    TOO_VERBOSE = "TOO_VERBOSE"
    OTHER = "OTHER"


class DocumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DELETED = "DELETED"


class VersionStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class SourceType(StrEnum):
    PDF = "PDF"
    TXT = "TXT"
    MARKDOWN = "MARKDOWN"
    HTML = "HTML"
    DOCX = "DOCX"


class SourceAuthority(StrEnum):
    OFFICIAL_POLICY = "OFFICIAL_POLICY"
    OFFICIAL_DOCUMENTATION = "OFFICIAL_DOCUMENTATION"
    PRODUCT_DOCUMENTATION = "PRODUCT_DOCUMENTATION"
    FAQ = "FAQ"
    SUPPORT_ARTICLE = "SUPPORT_ARTICLE"
    INTERNAL_GUIDE = "INTERNAL_GUIDE"
    LOW_PRIORITY = "LOW_PRIORITY"


class HandoffReason(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    USER_FRUSTRATED = "USER_FRUSTRATED"
    USER_DISPUTED_ANSWER = "USER_DISPUTED_ANSWER"
    REPEATED_FAILED_ANSWERS = "REPEATED_FAILED_ANSWERS"
    KNOWLEDGE_CONFLICT = "KNOWLEDGE_CONFLICT"
    SENSITIVE_REQUEST = "SENSITIVE_REQUEST"
    ACCOUNT_SPECIFIC = "ACCOUNT_SPECIFIC"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    AI_ESCALATION = "AI_ESCALATION"  # the grounded generator itself judged a human is needed
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
