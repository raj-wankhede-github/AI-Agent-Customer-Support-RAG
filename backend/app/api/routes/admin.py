from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.api.deps import AdminPrincipal, AppContainer, DbSession, StaffPrincipal, rate_limited
from app.core.errors import NotFoundError
from app.models import AuditLog, Handoff, User
from app.models.enums import ConversationStatus, HandoffStatus, UserRole
from app.repositories.conversations import paginate
from app.schemas.admin import (
    AdminConversationDetail,
    AdminConversationOut,
    AgentReplyRequest,
    AssignRequest,
    AuditLogOut,
    HandoffOut,
    MetricsOut,
    RetrievalDebugRequest,
    RetrievalDebugResponse,
    UserRef,
)
from app.schemas.common import Page, error_responses
from app.schemas.conversation import MessageOut
from app.services.admin import AdminService
from app.services.metrics import compute_metrics
from app.services.retrieval_debug import debug_retrieval

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin & support agents"],
    dependencies=[Depends(rate_limited("admin"))],
    responses=error_responses(401, 403, 429),
)
service = AdminService()


@router.get("/conversations", response_model=Page[AdminConversationOut], summary="All conversations in the tenant")
async def list_conversations(
    principal: StaffPrincipal, session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[ConversationStatus | None, Query(alias="status")] = None,
    handoff_status: HandoffStatus | None = None,
) -> Page[AdminConversationOut]:  # fmt: skip
    return await service.conversations(session, principal, page, page_size, q, status_filter, handoff_status)


@router.get(
    "/conversations/{conversation_id}",
    response_model=AdminConversationDetail,
    summary="Conversation with full history, handoffs and RAG traces",
    description="Traces show the normalized query, retrieved chunks with scores, selected evidence, validation and "
    "confidence for every assistant turn. They contain concise operational reasons, never model chain-of-thought.",
    responses=error_responses(404),
)
async def get_conversation(
    conversation_id: uuid.UUID, principal: StaffPrincipal, session: DbSession
) -> AdminConversationDetail:
    return await service.conversation_detail(session, principal, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reply to the customer as a human agent",
    responses=error_responses(404, 409, 422),
)
async def reply(
    conversation_id: uuid.UUID, body: AgentReplyRequest, principal: StaffPrincipal, session: DbSession
) -> MessageOut:
    return await service.reply(session, principal, conversation_id, body.content)


@router.post(
    "/conversations/{conversation_id}/resolve",
    response_model=AdminConversationOut,
    summary="Mark resolved (closes any open handoff; the assistant resumes on new messages)",
    responses=error_responses(404),
)
async def resolve(conversation_id: uuid.UUID, principal: StaffPrincipal, session: DbSession) -> AdminConversationOut:
    return await service.resolve(session, principal, conversation_id)


@router.post(
    "/conversations/{conversation_id}/close",
    response_model=AdminConversationOut,
    summary="Close a conversation",
    responses=error_responses(404),
)
async def close(conversation_id: uuid.UUID, principal: StaffPrincipal, session: DbSession) -> AdminConversationOut:
    return await service.close(session, principal, conversation_id)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete a conversation (admin only)",
    responses=error_responses(404),
)
async def delete_conversation(conversation_id: uuid.UUID, principal: AdminPrincipal, session: DbSession) -> Response:
    await service.delete_conversation(session, principal, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/handoffs",
    response_model=Page[HandoffOut],
    summary="Human handoff queue",
    description="Open (PENDING and ASSIGNED) handoffs by default, most urgent and longest-waiting first.",
)
async def list_handoffs(
    principal: StaffPrincipal, session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[HandoffStatus | None, Query(alias="status")] = None,
) -> Page[HandoffOut]:  # fmt: skip
    return await service.handoffs(session, principal, page, page_size, status_filter)


@router.post(
    "/handoffs/{handoff_id}/assign",
    response_model=HandoffOut,
    summary="Assign a handoff",
    responses=error_responses(404, 409, 422),
)
async def assign(
    handoff_id: uuid.UUID, body: AssignRequest, principal: StaffPrincipal, session: DbSession
) -> HandoffOut:
    return await service.assign(session, principal, handoff_id, body.agent_id)


@router.post(
    "/handoffs/{handoff_id}/resolve",
    response_model=AdminConversationOut,
    summary="Resolve a handoff",
    responses=error_responses(404),
)
async def resolve_handoff(handoff_id: uuid.UUID, principal: StaffPrincipal, session: DbSession) -> AdminConversationOut:
    handoff = (
        await session.execute(
            select(Handoff).where(Handoff.id == handoff_id, Handoff.company_id == principal.company_id)
        )
    ).scalar_one_or_none()
    if handoff is None:
        raise NotFoundError("Handoff not found.")
    return await service.resolve(session, principal, handoff.conversation_id)


@router.get("/agents", response_model=list[UserRef], summary="Support staff available for assignment")
async def list_agents(principal: StaffPrincipal, session: DbSession) -> list[UserRef]:
    rows = (
        await session.execute(
            select(User).where(User.company_id == principal.company_id, User.role.in_([UserRole.ADMIN, UserRole.AGENT]),
                               User.is_active.is_(True)).order_by(User.name)
        )
    ).scalars().all()  # fmt: skip
    return [UserRef(id=u.id, name=u.name, email=u.email, role=UserRole(u.role)) for u in rows]


@router.get("/metrics", response_model=MetricsOut, summary="Operational and RAG quality metrics")
async def metrics(
    principal: StaffPrincipal, session: DbSession, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> MetricsOut:
    return await compute_metrics(session, principal.company_id, days)


@router.post(
    "/retrieval/debug",
    response_model=RetrievalDebugResponse,
    summary="Inspect retrieval for a query (admin only)",
    description="Runs query understanding, hybrid retrieval, reranking, evidence sufficiency and conflict detection "
    "without generating an answer.",
    responses=error_responses(422, 503),
)
async def retrieval_debug(
    body: RetrievalDebugRequest, principal: AdminPrincipal, container: AppContainer
) -> RetrievalDebugResponse:
    return await debug_retrieval(container, principal, body)


@router.get("/audit-logs", response_model=Page[AuditLogOut], summary="Security audit log (admin only)")
async def audit_logs(
    principal: AdminPrincipal, session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[AuditLogOut]:  # fmt: skip
    stmt = select(AuditLog).where(AuditLog.company_id == principal.company_id).order_by(AuditLog.created_at.desc())
    items, total = await paginate(session, stmt, page, page_size)
    return Page.build([AuditLogOut.model_validate(a, from_attributes=True) for a in items], total, page, page_size)
