"""Knowledge-base management: upload (with versioning and de-duplication), listing,
metadata updates, deactivation, soft deletion and reindexing.

De-duplication: the SHA-256 of the uploaded bytes is compared with non-failed versions
in the same tenant. Identical content returns the existing document (`duplicate=true`)
without storing, embedding or indexing anything again. To change only metadata (title,
authority, effective date...) use PATCH - no new version is needed for that.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.ingestion.normalize import title_from_filename
from app.ingestion.validation import validate_upload
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentStatus, SourceAuthority, VersionStatus
from app.repositories.conversations import paginate
from app.schemas.common import Page
from app.schemas.knowledge import ChunkPreview, DocumentDetail, DocumentOut, DocumentUpdate, UploadResponse, VersionOut
from app.security.principal import Principal
from app.services.audit import record_audit
from app.storage.base import ObjectStorage, build_object_key, safe_filename
from app.utils.hashing import sha256_bytes
from app.utils.text import truncate

_LIVE_VERSION_STATUSES = (VersionStatus.UPLOADED, VersionStatus.PROCESSING, VersionStatus.READY)


@dataclass
class UploadMetadata:
    title: str | None = None
    authority: SourceAuthority = SourceAuthority.SUPPORT_ARTICLE
    category: str | None = None
    product: str | None = None
    locale: str | None = None
    effective_date: date | None = None
    source_uri: str | None = None
    document_id: uuid.UUID | None = None  # set to upload a new version of an existing document


class KnowledgeService:
    def __init__(
        self, settings: Settings, storage: ObjectStorage, notify_worker: Callable[[], None] | None = None
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.notify_worker = notify_worker

    async def upload(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
        meta: UploadMetadata,
    ) -> UploadResponse:
        validated = validate_upload(filename, data, content_type, self.settings.max_upload_size)
        checksum = sha256_bytes(data)

        document: Document | None = None
        if meta.document_id:
            document = await self._get(session, principal.company_id, meta.document_id, lock=True)
            if document.status == DocumentStatus.DELETED:
                raise ConflictError("Deleted documents cannot receive new versions.")
            if document.source_type != validated.source_type:
                raise ValidationAppError("A new version must have the same file type as the document.")

        duplicate_stmt = (
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.company_id == principal.company_id,
                DocumentVersion.checksum == checksum,
                DocumentVersion.status.in_(_LIVE_VERSION_STATUSES),
                Document.status != DocumentStatus.DELETED,
            )
            .order_by(DocumentVersion.created_at.desc())
            .limit(1)
        )
        if document is not None:
            duplicate_stmt = duplicate_stmt.where(Document.id == document.id)
        duplicate = (await session.execute(duplicate_stmt)).first()
        if duplicate is not None:
            version, existing = duplicate
            record_audit(
                session,
                action="document.upload_duplicate",
                company_id=principal.company_id,
                actor_user_id=principal.user_id,
                target_type="document",
                target_id=existing.id,
                details={"version_id": str(version.id)},
            )
            await session.commit()
            return UploadResponse(
                document=await self.document_out(session, existing), version=VersionOut.model_validate(version), duplicate=True,
                message="This exact file is already in the knowledge base, so it was not processed again.",
            )  # fmt: skip

        if document is None:
            document = Document(
                id=uuid.uuid4(), company_id=principal.company_id,
                title=truncate((meta.title or title_from_filename(filename)).strip(), 300),
                source_type=validated.source_type, source_uri=meta.source_uri, authority=meta.authority,
                category=meta.category, product=meta.product, locale=meta.locale, effective_date=meta.effective_date,
                status=DocumentStatus.ACTIVE, latest_version_number=0, metadata_={}, created_by=principal.user_id,
            )  # fmt: skip
            session.add(document)
        document.latest_version_number += 1
        version_id = uuid.uuid4()
        version = DocumentVersion(
            id=version_id, company_id=principal.company_id, document_id=document.id,
            version_number=document.latest_version_number, filename=safe_filename(filename, 255),
            mime_type=validated.mime_type, size_bytes=len(data), checksum=checksum,
            storage_key=build_object_key(principal.company_id, document.id, version_id, filename),
            status=VersionStatus.UPLOADED, attempts=0, chunk_count=0, effective_date=meta.effective_date,
            metadata_={}, created_by=principal.user_id,
        )  # fmt: skip
        session.add(version)
        await session.flush()
        # Store the object before committing: a storage failure rolls back the row.
        await self.storage.put(version.storage_key, data, validated.mime_type)
        record_audit(
            session, action="document.uploaded", company_id=principal.company_id, actor_user_id=principal.user_id,
            target_type="document", target_id=document.id,
            details={"version": version.version_number, "size_bytes": len(data), "source_type": validated.source_type.value},
        )  # fmt: skip
        await session.commit()
        if self.notify_worker:
            self.notify_worker()
        return UploadResponse(
            document=await self.document_out(session, document), version=VersionOut.model_validate(version), duplicate=False,
            message="Upload received. The document will become searchable once processing finishes.",
        )  # fmt: skip

    async def list(
        self,
        session: AsyncSession,
        principal: Principal,
        page: int,
        page_size: int,
        q: str | None,
        status: DocumentStatus | None,
    ) -> Page[DocumentOut]:
        stmt = select(Document).where(Document.company_id == principal.company_id)
        stmt = (
            stmt.where(Document.status == status) if status else stmt.where(Document.status != DocumentStatus.DELETED)
        )
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(
                or_(
                    Document.title.ilike(pattern, escape="\\"),
                    Document.category.ilike(pattern, escape="\\"),
                    Document.product.ilike(pattern, escape="\\"),
                )
            )
        items, total = await paginate(session, stmt.order_by(Document.updated_at.desc()), page, page_size)
        return Page.build([await self.document_out(session, d) for d in items], total, page, page_size)

    async def detail(self, session: AsyncSession, principal: Principal, document_id: uuid.UUID) -> DocumentDetail:
        document = await self._get(session, principal.company_id, document_id)
        versions = (
            await session.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.version_number.desc())
            )
        ).scalars().all()  # fmt: skip
        chunks: list[ChunkPreview] = []
        if document.active_version_id:
            rows = (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_version_id == document.active_version_id)
                    .order_by(DocumentChunk.chunk_index).limit(50)
                )
            ).scalars().all()  # fmt: skip
            chunks = [
                ChunkPreview(chunk_index=c.chunk_index, section_title=c.section_title, heading_path=c.heading_path,
                             page_number=c.page_number, token_count=c.token_count, content=truncate(c.content, 600),
                             metadata=c.metadata_ or {})
                for c in rows
            ]  # fmt: skip
        return DocumentDetail(
            document=await self.document_out(session, document),
            versions=[VersionOut.model_validate(v) for v in versions],
            chunks=chunks,
        )

    async def update(
        self, session: AsyncSession, principal: Principal, document_id: uuid.UUID, body: DocumentUpdate
    ) -> DocumentOut:
        document = await self._get(session, principal.company_id, document_id, lock=True)
        if document.status == DocumentStatus.DELETED:
            raise ConflictError("Deleted documents cannot be modified.")
        changes = body.model_dump(exclude_unset=True)
        if changes.get("status") == DocumentStatus.DELETED:
            raise ValidationAppError("Use DELETE to delete a document.")
        for field, value in changes.items():
            setattr(document, field, value)
        record_audit(
            session,
            action="document.updated",
            company_id=principal.company_id,
            actor_user_id=principal.user_id,
            target_type="document",
            target_id=document.id,
            details={"fields": sorted(changes)},
        )
        await session.commit()
        return await self.document_out(session, document)

    async def delete(self, session: AsyncSession, principal: Principal, document_id: uuid.UUID) -> None:
        """Soft delete: immediately removed from retrieval; versions and files are kept for audit."""
        document = await self._get(session, principal.company_id, document_id, lock=True)
        if document.status == DocumentStatus.DELETED:
            return
        document.status = DocumentStatus.DELETED
        record_audit(
            session,
            action="document.deleted",
            company_id=principal.company_id,
            actor_user_id=principal.user_id,
            target_type="document",
            target_id=document.id,
        )
        await session.commit()

    async def reindex(
        self, session: AsyncSession, principal: Principal | None, document_id: uuid.UUID, company_id: uuid.UUID
    ) -> VersionOut:
        """Queue a fresh version built from the current file. The existing active version keeps
        serving answers until the new one is fully processed."""
        document = await self._get(session, company_id, document_id, lock=True)
        if document.status == DocumentStatus.DELETED:
            raise ConflictError("Deleted documents cannot be reindexed.")
        in_flight = await session.scalar(
            select(func.count()).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.status.in_([VersionStatus.UPLOADED, VersionStatus.PROCESSING]),
            )
        )
        if in_flight:
            raise ConflictError("A version of this document is already being processed.")
        source = (
            await session.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
                .order_by((DocumentVersion.id == document.active_version_id).desc(), DocumentVersion.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()  # fmt: skip
        if source is None:
            raise ConflictError("This document has no stored file to reindex.")
        document.latest_version_number += 1
        version = DocumentVersion(
            company_id=company_id, document_id=document.id, version_number=document.latest_version_number,
            filename=source.filename, mime_type=source.mime_type, size_bytes=source.size_bytes, checksum=source.checksum,
            storage_key=source.storage_key, status=VersionStatus.UPLOADED, attempts=0, chunk_count=0,
            effective_date=source.effective_date, metadata_={"reindex_of": str(source.id)},
            created_by=principal.user_id if principal else None,
        )  # fmt: skip
        session.add(version)
        record_audit(
            session,
            action="document.reindex_requested",
            company_id=company_id,
            actor_user_id=principal.user_id if principal else None,
            target_type="document",
            target_id=document.id,
            details={"from_version": source.version_number},
        )
        await session.commit()
        if self.notify_worker:
            self.notify_worker()
        return VersionOut.model_validate(version)

    async def document_out(self, session: AsyncSession, document: Document) -> DocumentOut:
        latest = (
            await session.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_number.desc()).limit(1)
            )
        ).scalar_one_or_none()  # fmt: skip
        active_number = None
        if document.active_version_id:
            active_number = await session.scalar(
                select(DocumentVersion.version_number).where(DocumentVersion.id == document.active_version_id)
            )
        if document.status != DocumentStatus.ACTIVE:
            processing_status = document.status.value if hasattr(document.status, "value") else str(document.status)
        elif latest is None:
            processing_status = VersionStatus.UPLOADED.value
        elif latest.status == VersionStatus.SUPERSEDED:
            processing_status = VersionStatus.READY.value
        else:
            processing_status = str(latest.status)
        return DocumentOut(
            id=document.id, title=document.title, source_type=document.source_type, source_uri=document.source_uri,
            authority=document.authority, category=document.category, product=document.product, locale=document.locale,
            effective_date=document.effective_date, status=document.status, processing_status=processing_status,
            searchable=document.status == DocumentStatus.ACTIVE and document.active_version_id is not None,
            active_version_number=active_number, latest_version=VersionOut.model_validate(latest) if latest else None,
            created_at=document.created_at, updated_at=document.updated_at,
        )  # fmt: skip

    @staticmethod
    async def _get(
        session: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID, *, lock: bool = False
    ) -> Document:
        stmt = select(Document).where(Document.id == document_id, Document.company_id == company_id)
        document = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if document is None:
            raise NotFoundError("Document not found.")
        return document
