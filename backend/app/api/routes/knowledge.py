from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from app.api.deps import AdminPrincipal, AppContainer, DbSession, rate_limited
from app.core.errors import PayloadTooLargeError
from app.models.enums import DocumentStatus, SourceAuthority
from app.schemas.common import Page, error_responses
from app.schemas.knowledge import DocumentDetail, DocumentOut, DocumentUpdate, UploadResponse, VersionOut
from app.services.knowledge import KnowledgeService, UploadMetadata

router = APIRouter(prefix="/api/knowledge/documents", tags=["Knowledge base (admin)"])


def _service(container: AppContainer) -> KnowledgeService:
    worker = getattr(container, "worker", None)
    return KnowledgeService(container.settings, container.storage, worker.notify if worker else None)


Service = Annotated[KnowledgeService, Depends(_service)]


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document (or a new version)",
    description=(
        "Accepts PDF, TXT, Markdown, HTML and DOCX. The file is validated (size, extension, content signature, "
        "active-content scan), stored, and queued for background ingestion; it is not searchable until its status "
        "is READY. Pass `document_id` to upload a new version of an existing document - the current version keeps "
        "serving answers until the new one is ready. Identical content is detected by checksum and not re-processed."
    ),
    responses=error_responses(401, 403, 404, 409, 413, 415, 422, 429),
    dependencies=[Depends(rate_limited("upload"))],
)
async def upload_document(
    principal: AdminPrincipal, session: DbSession, service: Service, container: AppContainer,
    file: Annotated[UploadFile, File(description="The document file")],
    title: Annotated[str | None, Form(max_length=300)] = None,
    authority: Annotated[SourceAuthority, Form()] = SourceAuthority.SUPPORT_ARTICLE,
    category: Annotated[str | None, Form(max_length=100)] = None,
    product: Annotated[str | None, Form(max_length=100)] = None,
    locale: Annotated[str | None, Form(max_length=20)] = None,
    effective_date: Annotated[date | None, Form()] = None,
    source_uri: Annotated[str | None, Form(max_length=1000)] = None,
    document_id: Annotated[uuid.UUID | None, Form()] = None,
) -> UploadResponse:  # fmt: skip
    limit = container.settings.max_upload_size
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLargeError(f"The file exceeds the maximum upload size of {limit // (1024 * 1024)} MB.")
    meta = UploadMetadata(
        title=title or None, authority=authority, category=category or None, product=product or None,
        locale=locale or None, effective_date=effective_date, source_uri=source_uri or None, document_id=document_id,
    )  # fmt: skip
    return await service.upload(
        session, principal, filename=file.filename or "document", content_type=file.content_type, data=data, meta=meta
    )


@router.get("", response_model=Page[DocumentOut], summary="List documents", responses=error_responses(401, 403))
async def list_documents(
    principal: AdminPrincipal, session: DbSession, service: Service,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
) -> Page[DocumentOut]:  # fmt: skip
    return await service.list(session, principal, page, page_size, q, status_filter)


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    summary="Document detail: versions, ingestion errors, chunks",
    responses=error_responses(401, 403, 404),
)
async def get_document(
    document_id: uuid.UUID, principal: AdminPrincipal, session: DbSession, service: Service
) -> DocumentDetail:
    return await service.detail(session, principal, document_id)


@router.patch(
    "/{document_id}",
    response_model=DocumentOut,
    summary="Update metadata or activate/deactivate",
    responses=error_responses(401, 403, 404, 409, 422),
)
async def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, principal: AdminPrincipal, session: DbSession, service: Service
) -> DocumentOut:
    return await service.update(session, principal, document_id, body)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description="Soft delete: removed from retrieval immediately; versions are retained for audit.",
    responses=error_responses(401, 403, 404),
)
async def delete_document(
    document_id: uuid.UUID, principal: AdminPrincipal, session: DbSession, service: Service
) -> Response:
    await service.delete(session, principal, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/reindex",
    response_model=VersionOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reindex a document",
    description="Re-extracts, re-chunks and re-embeds the stored file as a new version.",
    responses=error_responses(401, 403, 404, 409),
)
async def reindex_document(
    document_id: uuid.UUID, principal: AdminPrincipal, session: DbSession, service: Service
) -> VersionOut:
    return await service.reindex(session, principal, document_id, principal.company_id)
