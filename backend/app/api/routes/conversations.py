from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import AppContainer, CurrentPrincipal, DbSession, rate_limited
from app.core.errors import AppError
from app.models.enums import ConversationStatus
from app.schemas.common import Page, error_responses
from app.schemas.conversation import (
    ChatResponse,
    ConversationCreate,
    ConversationCreated,
    ConversationDetail,
    ConversationOut,
    FeedbackRequest,
    HandoffRequest,
    SendMessageRequest,
)
from app.services.chat import ChatService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["Conversations"])
_KEEPALIVE_SECONDS = 10


def _service(container: AppContainer) -> ChatService:
    return ChatService(container)


Service = Annotated[ChatService, Depends(_service)]


@router.post(
    "",
    response_model=ConversationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation (or continue the same one)",
    description=(
        "Creates a conversation (201). If `first_message` matches the opening question of the caller's conversation "
        "that is not closed and was active recently, or the caller has an empty conversation, that conversation is "
        "returned instead (200, `reused: true`), so the same chat never appears twice."
    ),
    responses=error_responses(401, 422),
)
async def create_conversation(
    body: ConversationCreate, principal: CurrentPrincipal, session: DbSession, service: Service, response: Response
) -> ConversationCreated:
    conversation, reused = await service.create(session, principal, body.title, body.first_message)
    if reused:
        response.status_code = status.HTTP_200_OK
    return ConversationCreated(**conversation.model_dump(), reused=reused)


@router.get(
    "",
    response_model=Page[ConversationOut],
    summary="List my conversations",
    description="The caller's own conversations, newest activity first. `q` searches titles and message text.",
    responses=error_responses(401),
)
async def list_conversations(
    principal: CurrentPrincipal, session: DbSession, service: Service,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None, status_filter: Annotated[ConversationStatus | None, Query(alias="status")] = None,
) -> Page[ConversationOut]:  # fmt: skip
    return await service.list(session, principal, page, page_size, q, status_filter)


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get a conversation with its messages",
    responses=error_responses(401, 404),
)
async def get_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, service: Service
) -> ConversationDetail:
    return await service.detail(session, principal, conversation_id)


@router.post(
    "/{conversation_id}/messages",
    response_model=ChatResponse,
    summary="Send a message and get the assistant's response",
    description=(
        "Runs the grounded RAG pipeline. `answer_status` is ANSWERED (with citations), ABSTAINED (insufficient "
        "evidence), HANDOFF_REQUIRED (a human has been requested) or AWAITING_HUMAN (a human owns the conversation "
        "and the assistant stays silent). Supply `client_message_id` to make retries idempotent."
    ),
    responses=error_responses(401, 404, 409, 422, 429, 503),
    dependencies=[Depends(rate_limited("chat"))],
)
async def send_message(
    conversation_id: uuid.UUID, body: SendMessageRequest, principal: CurrentPrincipal, service: Service
) -> ChatResponse:
    return await service.send_message(principal, conversation_id, body.content, body.client_message_id)


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Send a message and stream the validated response (Server-Sent Events)",
    description=(
        "Events: `status` (pipeline progress), `delta` (answer text), `done` (the full ChatResponse), `error`. "
        "The answer is generated, grounding-validated and persisted **before** any text is streamed, so a "
        "streamed answer can never contain unvalidated content."
    ),
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, **error_responses(401, 404, 409, 422, 429)},
    dependencies=[Depends(rate_limited("chat"))],
)
async def stream_message(
    conversation_id: uuid.UUID, body: SendMessageRequest, principal: CurrentPrincipal, service: Service
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        yield _sse("status", {"stage": "processing"})
        # Shielded: if the client disconnects, the turn still completes and is saved.
        task = asyncio.ensure_future(
            service.send_message(principal, conversation_id, body.content, body.client_message_id)
        )
        while True:
            done, _ = await asyncio.wait({task}, timeout=_KEEPALIVE_SECONDS)
            if done:
                break
            yield ": keepalive\n\n"
        try:
            result = task.result()
        except AppError as exc:
            yield _sse("error", {"code": exc.code.value, "message": exc.message, "status": exc.status_code})
            return
        except Exception:
            log.exception("stream_turn_failed")
            yield _sse(
                "error", {"code": "INTERNAL_ERROR", "message": "Something went wrong. Please try again.", "status": 500}
            )
            return
        if result.answer:
            for piece in re.findall(r"\S+\s*", result.answer):
                yield _sse("delta", {"text": piece})
                await asyncio.sleep(0.012)
        yield _sse("done", result.model_dump(mode="json"))

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post(
    "/{conversation_id}/handoff",
    response_model=ChatResponse,
    summary="Ask for a human agent",
    responses=error_responses(401, 404, 409),
)
async def request_handoff(
    conversation_id: uuid.UUID, body: HandoffRequest, principal: CurrentPrincipal, session: DbSession, service: Service
) -> ChatResponse:
    return await service.request_handoff(session, principal, conversation_id, body.note)


@router.post(
    "/{conversation_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rate a response",
    description="Helpful / not helpful with an optional reason. Re-submitting replaces the caller's previous rating.",
    responses=error_responses(401, 404, 422),
)
async def submit_feedback(
    conversation_id: uuid.UUID, body: FeedbackRequest, principal: CurrentPrincipal, session: DbSession, service: Service
) -> Response:
    await service.feedback(session, principal, conversation_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{conversation_id}/close",
    response_model=ConversationOut,
    summary="Close a conversation",
    description="Closed conversations stay in history but accept no new messages.",
    responses=error_responses(401, 404),
)
async def close_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, service: Service
) -> ConversationOut:
    return await service.close(session, principal, conversation_id)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation permanently",
    responses=error_responses(401, 404),
)
async def delete_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, service: Service
) -> Response:
    await service.delete(session, principal, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
