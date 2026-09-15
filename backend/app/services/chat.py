"""Customer conversations and the chat turn.

A chat turn is split into three phases so the database is never held open during LLM
calls and nothing is reported as saved unless it was:
  1. persist the customer message (commit) and load bounded context;
  2. run the agent (no DB transaction open);
  3. persist the assistant message, RAG trace, handoff and conversation state atomically.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import responses
from app.agents.policy import HandoffDecision
from app.agents.support_agent import AgentRequest, AgentResult
from app.core.container import Container
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.base import utcnow
from app.models import Conversation, Feedback, Handoff, Message, RagTrace, User
from app.models.enums import (
    AnswerStatus,
    ConversationStatus,
    HandoffPriority,
    HandoffReason,
    HandoffStatus,
    MessageRole,
    UserRole,
)
from app.rag.types import ConversationContext, HistoryTurn
from app.repositories import conversations as repo
from app.schemas.common import Page
from app.schemas.conversation import (
    ActorRef,
    ChatResponse,
    Citation,
    ConversationDetail,
    ConversationOut,
    FeedbackRequest,
    message_out,
)
from app.security.principal import Principal
from app.services.audit import record_audit
from app.services.lifecycle import close_conversation, display_name, reopen_if_resolved, touch
from app.utils.text import normalize_unicode, truncate

log = structlog.get_logger(__name__)
DEFAULT_TITLE = "New conversation"
_HUMAN_OWNED = (HandoffStatus.PENDING, HandoffStatus.ASSIGNED)


def derive_title(content: str) -> str:
    return truncate(re.sub(r"\s+", " ", content).strip(), 60) or DEFAULT_TITLE


class ChatService:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.settings = container.settings

    # --- conversations ------------------------------------------------------------------

    async def create(
        self, session: AsyncSession, principal: Principal, title: str | None, first_message: str | None = None
    ) -> tuple[ConversationOut, bool]:
        """Start a conversation, or return the customer's existing one for the same chat.

        Returns (conversation, reused). A conversation is reused when it is not closed, was
        active within CONVERSATION_REUSE_WINDOW_HOURS, and either has no messages yet or started
        with the same question - so repeating a question (e.g. clicking the same suggestion)
        continues that chat, reopening it if it was resolved, instead of creating a duplicate.
        """
        existing = await self._reusable(session, principal, first_message)
        if existing is not None:
            return (await conversation_outs(session, [existing]))[0], True
        conversation = Conversation(
            company_id=principal.company_id, user_id=principal.user_id, title=(title or DEFAULT_TITLE).strip() or DEFAULT_TITLE,
            status=ConversationStatus.OPEN, handoff_status=HandoffStatus.NONE,
            summarized_message_count=0, consecutive_failed_answers=0,
        )  # fmt: skip
        session.add(conversation)
        await session.commit()
        return ConversationOut.model_validate(conversation), False

    async def _reusable(
        self, session: AsyncSession, principal: Principal, first_message: str | None
    ) -> Conversation | None:
        window = self.settings.conversation_reuse_window_hours
        if window <= 0:
            return None
        candidates = (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.company_id == principal.company_id,
                    Conversation.user_id == principal.user_id,
                    Conversation.status != ConversationStatus.CLOSED,
                    Conversation.last_message_at >= utcnow() - timedelta(hours=window),
                )
                .order_by(Conversation.last_message_at.desc())
                .limit(20)
            )
        ).scalars().all()  # fmt: skip
        if not candidates:
            return None
        firsts = await repo.first_user_messages(session, [c.id for c in candidates])
        target = _normalize_question(first_message) if first_message else None
        for conversation in candidates:
            opening = firsts.get(conversation.id)
            if opening is None or (target and _normalize_question(opening) == target):
                return conversation
        return None

    async def list(
        self,
        session: AsyncSession,
        principal: Principal,
        page: int,
        page_size: int,
        q: str | None,
        status: ConversationStatus | None,
    ) -> Page[ConversationOut]:
        stmt = select(Conversation).where(
            Conversation.company_id == principal.company_id, Conversation.user_id == principal.user_id
        )
        stmt = stmt.where(repo.has_messages_clause())
        if q:
            stmt = stmt.where(repo.search_clause(q))
        if status:
            stmt = stmt.where(Conversation.status == status)
        items, total = await repo.paginate(session, stmt.order_by(Conversation.last_message_at.desc()), page, page_size)
        return Page.build(await conversation_outs(session, items), total, page, page_size)

    async def detail(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
    ) -> ConversationDetail:
        conversation = await repo.get_owned(session, principal, conversation_id)
        messages = await repo.messages_for(session, conversation.id)
        feedback = await repo.feedback_by_message(session, principal.user_id, [m.id for m in messages])
        authors = await repo.user_names(
            session, {m.author_user_id for m in messages if m.role == MessageRole.HUMAN_AGENT and m.author_user_id}
        )
        return ConversationDetail(
            conversation=(await conversation_outs(session, [conversation]))[0],
            messages=[
                message_out(m, author_name=_agent_display_name(authors.get(m.author_user_id)) if m.author_user_id else None,
                            feedback=feedback.get(m.id))
                for m in messages
            ],
        )  # fmt: skip

    async def close(self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID) -> ConversationOut:
        conversation = await repo.get_owned(session, principal, conversation_id, lock=True)
        await close_conversation(session, conversation, actor=principal)
        await session.commit()
        return (await conversation_outs(session, [conversation]))[0]

    async def delete(self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID) -> None:
        conversation = await repo.get_owned(session, principal, conversation_id, lock=True)
        record_audit(
            session,
            action="conversation.deleted",
            company_id=principal.company_id,
            actor_user_id=principal.user_id,
            target_type="conversation",
            target_id=conversation.id,
        )
        await session.delete(conversation)
        await session.commit()

    async def feedback(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID, body: FeedbackRequest
    ) -> None:
        conversation = await repo.get_owned(session, principal, conversation_id)
        message = await session.get(Message, body.message_id)
        if message is None or message.conversation_id != conversation.id:
            raise NotFoundError("Message not found.")
        if message.role not in (MessageRole.ASSISTANT, MessageRole.HUMAN_AGENT):
            raise ValidationAppError("Feedback can only be given on support responses.")
        existing = (
            await session.execute(
                select(Feedback).where(Feedback.message_id == message.id, Feedback.user_id == principal.user_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = Feedback(
                company_id=principal.company_id,
                conversation_id=conversation.id,
                message_id=message.id,
                user_id=principal.user_id,
            )
            session.add(existing)
        existing.rating = body.rating
        existing.reason = body.reason.value if body.reason else None
        existing.comment = body.comment
        await session.commit()

    async def request_handoff(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID, note: str | None
    ) -> ChatResponse:
        conversation = await repo.get_owned(session, principal, conversation_id, lock=True)
        if conversation.status == ConversationStatus.CLOSED:
            raise ConflictError("This conversation is closed. Start a new conversation.")
        reopen_if_resolved(session, conversation, principal, utcnow())
        existing = await repo.open_handoff(session, conversation.id)
        last_user = (
            await session.execute(
                select(Message).where(Message.conversation_id == conversation.id, Message.role == MessageRole.USER)
                .order_by(Message.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()  # fmt: skip
        assistant = Message(
            company_id=principal.company_id, conversation_id=conversation.id, role=MessageRole.ASSISTANT,
            content=responses.HANDOFF_USER_REQUESTED, answer_status=AnswerStatus.HANDOFF_REQUIRED,
            handoff_reason=HandoffReason.USER_REQUESTED, provider="policy", citations=[],
            retrieval_metadata={"response_kind": "HANDOFF", "trigger": "customer_button"},
        )  # fmt: skip
        session.add(assistant)
        if existing is None:
            decision = HandoffDecision(
                HandoffReason.USER_REQUESTED,
                "Customer asked to speak with a human." + (f" Note: {truncate(note, 300)}" if note else ""),
                HandoffPriority.NORMAL,
            )
            await session.flush()
            open_handoff(session, conversation, decision, last_user.id if last_user else None, principal)
        _touch(conversation, assistant.content)
        await session.commit()
        return self._response(conversation, last_user.id if last_user else assistant.id, assistant)

    # --- chat turn ----------------------------------------------------------------------

    async def send_message(
        self, principal: Principal, conversation_id: uuid.UUID, content: str, client_message_id: str | None
    ) -> ChatResponse:
        content = content.strip()
        if not content:
            raise ValidationAppError("Message cannot be empty.")
        if len(content) > self.settings.max_user_message_chars:
            raise ValidationAppError(f"Messages are limited to {self.settings.max_user_message_chars} characters.")

        # Phase 1: persist the customer message.
        async with self.c.sessions() as session:
            conversation = await repo.get_owned(session, principal, conversation_id, lock=True)
            if conversation.status == ConversationStatus.CLOSED:
                raise ConflictError("This conversation is closed. Start a new conversation to continue.")
            if client_message_id:
                replay = await self._replay(session, conversation, client_message_id)
                if replay is not None:
                    return replay
            now = utcnow()
            # Writing into a resolved conversation reopens it; the event precedes the message.
            reopen_if_resolved(session, conversation, principal, now)
            user_message = Message(
                id=uuid.uuid4(), company_id=principal.company_id, conversation_id=conversation.id, role=MessageRole.USER,
                content=content, author_user_id=principal.user_id, client_message_id=client_message_id, citations=[],
                retrieval_metadata={}, created_at=now + timedelta(milliseconds=1),
            )  # fmt: skip
            session.add(user_message)
            if conversation.title == DEFAULT_TITLE:
                conversation.title = derive_title(content)
            if conversation.status == ConversationStatus.WAITING_FOR_CUSTOMER:
                conversation.status = (
                    ConversationStatus.WAITING_FOR_HUMAN
                    if conversation.handoff_status in _HUMAN_OWNED
                    else ConversationStatus.OPEN
                )
            _touch(conversation, content)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if client_message_id:
                    replay = await self._replay(
                        session, await repo.get_owned(session, principal, conversation_id), client_message_id
                    )
                    if replay is not None:
                        return replay
                raise
            if conversation.handoff_status in _HUMAN_OWNED:
                return ChatResponse(
                    conversation_id=conversation.id, user_message_id=user_message.id, message_id=None, answer=None,
                    citations=[], answer_status=AnswerStatus.AWAITING_HUMAN, confidence=None, handoff_required=True,
                    handoff_reason=None, conversation_status=ConversationStatus(conversation.status),
                )  # fmt: skip
            context = await self._context(session, conversation, exclude=user_message.id)

        # Phase 2: run the agent with no transaction open.
        result = await self._run_agent(principal, conversation_id, content, context)

        # Phase 3: persist the outcome atomically.
        async with self.c.sessions() as session, session.begin():
            conversation = await repo.get_owned(session, principal, conversation_id, lock=True)
            stored_user = await session.get(Message, user_message.id)
            if stored_user is not None and result.analysis is not None:
                stored_user.retrieval_metadata = {"standalone_query": result.analysis.standalone_query}
            assistant = self._assistant_message(principal, conversation, user_message.id, result)
            session.add(assistant)
            await session.flush()
            session.add(
                RagTrace(
                    company_id=principal.company_id,
                    conversation_id=conversation.id,
                    user_message_id=user_message.id,
                    assistant_message_id=assistant.id,
                    **_trace_columns(result.trace),
                )
            )
            if result.handoff is not None and await repo.open_handoff(session, conversation.id) is None:
                open_handoff(session, conversation, result.handoff, user_message.id, principal)
            if result.counts_as_failure:
                conversation.consecutive_failed_answers += 1
            elif result.kind == "RAG_ANSWER":
                conversation.consecutive_failed_answers = 0
            _touch(conversation, assistant.content)
            response = self._response(conversation, user_message.id, assistant)

        await self._update_summary(conversation_id)
        return response

    async def _run_agent(
        self, principal: Principal, conversation_id: uuid.UUID, content: str, context: ConversationContext
    ) -> AgentResult:
        request = AgentRequest(
            company_id=principal.company_id, conversation_id=conversation_id, message=content, context=context
        )
        try:
            return await asyncio.wait_for(self.c.agent.respond(request), timeout=self.settings.chat_timeout_seconds)
        except Exception as exc:  # the customer must get a safe response, never a guessed one
            log.exception("agent_failed", conversation_id=str(conversation_id), error=type(exc).__name__)
            detail = "Timed out" if isinstance(exc, TimeoutError) else f"Unexpected agent error ({type(exc).__name__})"
            decision = HandoffDecision(
                HandoffReason.PROVIDER_FAILURE, f"{detail}; no answer was generated.", HandoffPriority.NORMAL
            )
            return AgentResult(
                status=AnswerStatus.HANDOFF_REQUIRED, kind="ERROR", content=responses.PROVIDER_UNAVAILABLE_HANDOFF,
                reason=decision.detail, handoff=decision, counts_as_failure=True,
                trace={"original_query": content, "standalone_query": content, "decision": "HANDOFF_REQUIRED",
                       "decision_reason": decision.detail, "handoff_reason": decision.reason.value},
            )  # fmt: skip

    async def _context(
        self, session: AsyncSession, conversation: Conversation, exclude: uuid.UUID
    ) -> ConversationContext:
        window = self.settings.conversation_history_messages
        rows = (
            await session.execute(
                select(Message).where(Message.conversation_id == conversation.id, Message.id != exclude,
                                      Message.role != MessageRole.SYSTEM)
                .order_by(Message.created_at.desc()).limit(window)
            )
        ).scalars().all()  # fmt: skip
        return ConversationContext(
            summary=conversation.summary,
            recent=[_turn(m) for m in reversed(rows)],
            consecutive_failed_answers=conversation.consecutive_failed_answers,
        )

    async def _update_summary(self, conversation_id: uuid.UUID) -> None:
        """Fold turns that fell out of the recent-history window into the rolling summary."""
        window = self.settings.conversation_history_messages
        try:
            async with self.c.sessions() as session:
                conversation = await session.get(Conversation, conversation_id)
                if conversation is None:
                    return
                total = await repo.message_count(session, conversation_id)
                target = total - window
                if target <= conversation.summarized_message_count:
                    return
                rows = (
                    await session.execute(
                        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
                        .offset(conversation.summarized_message_count).limit(target - conversation.summarized_message_count)
                    )
                ).scalars().all()  # fmt: skip
                summary = await self.c.summarizer.summarize(conversation.summary, [_turn(m) for m in rows])
                conversation.summary = summary
                conversation.summarized_message_count = target
                await session.commit()
        except Exception:
            log.exception("summary_update_failed", conversation_id=str(conversation_id))

    async def _replay(
        self, session: AsyncSession, conversation: Conversation, client_message_id: str
    ) -> ChatResponse | None:
        user_message = await repo.find_by_client_id(session, conversation.id, client_message_id)
        if user_message is None:
            return None
        reply = await repo.reply_to(session, user_message.id)
        if reply is None:
            if conversation.handoff_status in _HUMAN_OWNED:
                return ChatResponse(
                    conversation_id=conversation.id, user_message_id=user_message.id, message_id=None, answer=None, citations=[],
                    answer_status=AnswerStatus.AWAITING_HUMAN, confidence=None, handoff_required=True, handoff_reason=None,
                    conversation_status=ConversationStatus(conversation.status),
                )  # fmt: skip
            raise ConflictError("This message is still being processed. Please wait a moment.")
        return self._response(conversation, user_message.id, reply)

    def _assistant_message(
        self, principal: Principal, conversation: Conversation, user_message_id: uuid.UUID, result: AgentResult
    ) -> Message:
        trace = result.trace
        metadata: dict[str, Any] = {
            "response_kind": result.kind,
            "reason": truncate(result.reason, 300),
            "retrieval_count": len(trace.get("candidates") or []),
            "top_score": (trace.get("sufficiency") or {}).get("top_score"),
            "selected_sources": [
                {"document_title": e.get("document_title"), "chunk_id": e.get("chunk_id")}
                for e in trace.get("selected_evidence") or []
            ],
            "validation_passed": (trace.get("validation") or {}).get("passed"),
            "abstention_reason": result.reason if result.status == AnswerStatus.ABSTAINED else None,
            "confidence_score": result.confidence.score if result.confidence else None,
            "prompt_versions": result.prompt_versions,
            "timings_ms": result.timings,
        }
        return Message(
            id=uuid.uuid4(), company_id=principal.company_id, conversation_id=conversation.id, role=MessageRole.ASSISTANT,
            content=result.content, reply_to_message_id=user_message_id, answer_status=result.status,
            confidence=result.confidence.level if result.confidence else None,
            confidence_score=round(result.confidence.score, 4) if result.confidence else None,
            citations=result.citations, retrieval_metadata=metadata,
            handoff_reason=result.handoff.reason.value if result.handoff else None,
            provider=result.provider, model=result.model,
            prompt_version=truncate(";".join(sorted(result.prompt_versions.values())), 200) or None,
            latency_ms=result.latency_ms, input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
            created_at=utcnow(),
        )  # fmt: skip

    @staticmethod
    def _response(conversation: Conversation, user_message_id: uuid.UUID, assistant: Message) -> ChatResponse:
        out = message_out(assistant)
        return ChatResponse(
            conversation_id=conversation.id,
            user_message_id=user_message_id,
            message_id=assistant.id,
            answer=assistant.content,
            citations=[Citation.model_validate(c) for c in assistant.citations or []],
            answer_status=AnswerStatus(assistant.answer_status) if assistant.answer_status else AnswerStatus.ANSWERED,
            confidence=out.confidence,
            handoff_required=assistant.answer_status == AnswerStatus.HANDOFF_REQUIRED,
            handoff_reason=assistant.handoff_reason,
            conversation_status=ConversationStatus(conversation.status),
            message=out,
        )


def open_handoff(
    session: AsyncSession,
    conversation: Conversation,
    decision: HandoffDecision,
    triggering_message_id: uuid.UUID | None,
    actor: Principal | None,
) -> Handoff:
    handoff = Handoff(
        company_id=conversation.company_id, conversation_id=conversation.id, triggering_message_id=triggering_message_id,
        reason_code=decision.reason.value, reason_detail=truncate(decision.detail, 500), priority=decision.priority,
        status=HandoffStatus.PENDING,
    )  # fmt: skip
    session.add(handoff)
    conversation.status = ConversationStatus.WAITING_FOR_HUMAN
    conversation.handoff_status = HandoffStatus.PENDING
    record_audit(
        session, action="conversation.handoff_created", company_id=conversation.company_id,
        actor_user_id=actor.user_id if actor else None, target_type="conversation", target_id=conversation.id,
        details={"reason_code": decision.reason.value, "priority": decision.priority.value},
    )  # fmt: skip
    return handoff


def _touch(conversation: Conversation, content: str) -> None:
    touch(conversation, content)


def _normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_unicode(text)).strip().strip("?.!").strip().lower()


def _turn(message: Message) -> HistoryTurn:
    return HistoryTurn(
        role=MessageRole(message.role),
        content=message.content,
        standalone_query=(message.retrieval_metadata or {}).get("standalone_query"),
        answer_status=message.answer_status,
    )


def _agent_display_name(user: Any) -> str:
    return f"{user.name.split()[0]} (Support)" if user and user.name else "Support team"


def actor_out(user: User | None, *, full_name: bool = False) -> ActorRef | None:
    if user is None:
        return None
    role = UserRole(user.role)
    return ActorRef(id=user.id, name=user.name if full_name else display_name(user.name, role), role=role)


async def conversation_outs(session: AsyncSession, conversations: list[Conversation]) -> list[ConversationOut]:
    """Customer-facing conversation views (staff names shown as 'First (Support)')."""
    actor_ids = {c.closed_by_user_id for c in conversations if c.closed_by_user_id} | {
        c.resolved_by_user_id for c in conversations if c.resolved_by_user_id
    }
    users = await repo.user_names(session, actor_ids)
    return [
        ConversationOut.model_validate(c).model_copy(
            update={
                "closed_by": actor_out(users.get(c.closed_by_user_id)) if c.closed_by_user_id else None,
                "resolved_by": actor_out(users.get(c.resolved_by_user_id)) if c.resolved_by_user_id else None,
            }
        )
        for c in conversations
    ]


_TRACE_KEYS = (
    "original_query", "standalone_query", "analysis", "candidates", "selected_evidence", "sufficiency", "conflicts",
    "validation", "confidence", "decision", "decision_reason", "handoff_reason", "timings_ms", "prompt_versions",
    "provider", "model", "embedding_model", "input_tokens", "output_tokens",
)  # fmt: skip
_TRACE_DEFAULTS: dict[str, Any] = {
    "analysis": {}, "candidates": [], "selected_evidence": [], "sufficiency": {}, "conflicts": [], "validation": {},
    "confidence": {}, "timings_ms": {}, "prompt_versions": {}, "input_tokens": 0, "output_tokens": 0, "decision": "ERROR",
}  # fmt: skip


def _trace_columns(trace: dict[str, Any]) -> dict[str, Any]:
    columns = {key: trace.get(key, _TRACE_DEFAULTS.get(key)) for key in _TRACE_KEYS}
    columns["original_query"] = columns["original_query"] or ""
    columns["standalone_query"] = columns["standalone_query"] or columns["original_query"]
    return columns
