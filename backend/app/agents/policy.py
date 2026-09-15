"""Safety and handoff policy decided before retrieval.

These rules are deterministic on purpose: whether a customer gets a human for an
explicit request, a security incident or an account-specific action must not depend
on a model's judgment.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents import responses
from app.models.enums import AnswerStatus, HandoffPriority, HandoffReason
from app.rag.types import ConversationContext, Intent, QueryAnalysis

# Categories answered only from authoritative sources with HIGH confidence, else escalated.
AUTHORITATIVE_ONLY_CATEGORIES = frozenset({"legal", "financial", "medical"})
AUTHORITATIVE_SOURCES = ["OFFICIAL_POLICY", "OFFICIAL_DOCUMENTATION"]


@dataclass
class HandoffDecision:
    reason: HandoffReason
    detail: str
    priority: HandoffPriority


@dataclass
class PolicyOutcome:
    status: AnswerStatus
    kind: str
    content: str
    reason: str
    handoff: HandoffDecision | None = None


def _handoff(reason: HandoffReason, detail: str, priority: HandoffPriority, content: str) -> PolicyOutcome:
    return PolicyOutcome(
        AnswerStatus.HANDOFF_REQUIRED, "HANDOFF", content, detail, HandoffDecision(reason, detail, priority)
    )


def pre_retrieval_policy(analysis: QueryAnalysis, context: ConversationContext) -> PolicyOutcome | None:
    if analysis.injection_flags:
        return PolicyOutcome(
            AnswerStatus.ABSTAINED,
            "REFUSAL",
            responses.REFUSAL_INJECTION,
            f"Prompt-injection pattern detected in the customer message ({', '.join(analysis.injection_flags)}).",
        )
    if analysis.handoff_requested or analysis.intent is Intent.HANDOFF_REQUEST:
        return _handoff(
            HandoffReason.USER_REQUESTED,
            "Customer asked to speak with a human.",
            HandoffPriority.NORMAL,
            responses.HANDOFF_USER_REQUESTED,
        )
    if analysis.sensitive_category == "security_incident":
        return _handoff(
            HandoffReason.SENSITIVE_REQUEST,
            "Possible security incident reported by the customer.",
            HandoffPriority.URGENT,
            responses.HANDOFF_SECURITY,
        )
    if analysis.sensitive_category in ("identity_verification", "account_ownership"):
        return _handoff(
            HandoffReason.SENSITIVE_REQUEST,
            f"Request involves {analysis.sensitive_category.replace('_', ' ')}, which requires a human.",
            HandoffPriority.HIGH,
            responses.HANDOFF_SENSITIVE,
        )
    if analysis.intent is Intent.ACCOUNT_ACTION:
        return _handoff(
            HandoffReason.ACCOUNT_SPECIFIC,
            "Request needs access to the customer's own account or order data.",
            HandoffPriority.NORMAL,
            responses.HANDOFF_ACCOUNT,
        )
    if analysis.disputes_previous_answer:
        return _handoff(
            HandoffReason.USER_DISPUTED_ANSWER,
            "Customer disputed the previous answer.",
            HandoffPriority.HIGH,
            responses.HANDOFF_DISPUTE,
        )
    if analysis.frustration:
        return _handoff(
            HandoffReason.USER_FRUSTRATED,
            "Customer appears frustrated.",
            HandoffPriority.HIGH,
            responses.HANDOFF_FRUSTRATED,
        )
    if analysis.intent is Intent.GREETING:
        return PolicyOutcome(AnswerStatus.ANSWERED, "CONVERSATIONAL", responses.GREETING, "Greeting.")
    if analysis.intent is Intent.THANKS:
        return PolicyOutcome(AnswerStatus.ANSWERED, "CONVERSATIONAL", responses.THANKS, "Acknowledgement.")
    if analysis.requires_clarification:
        return PolicyOutcome(
            AnswerStatus.ANSWERED,
            "CLARIFICATION",
            analysis.clarification_question or responses.CLARIFY,
            "Question too vague to search the knowledge base.",
        )
    return None


def repeated_failure_handoff(context: ConversationContext, threshold: int) -> HandoffDecision | None:
    """Called when this turn failed to produce an answer."""
    if context.consecutive_failed_answers + 1 >= threshold:
        return HandoffDecision(
            HandoffReason.REPEATED_FAILED_ANSWERS,
            f"{context.consecutive_failed_answers + 1} consecutive questions could not be answered from the knowledge base.",
            HandoffPriority.NORMAL,
        )
    return None
