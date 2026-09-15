"""Document ingestion: extraction -> normalization -> metadata -> chunking -> embedding
-> persistence -> validation -> activation.

Guarantees:
- A version is claimed with FOR UPDATE SKIP LOCKED, so several workers never process the
  same version.
- Chunks, embeddings, the READY status and activation are written in one transaction, and
  retrieval only reads a document's `active_version_id`; partially processed content is
  never searchable.
- The previously active version stays active until the new one is fully validated.
- Failures record a safe error code/message and leave the version unsearchable; transient
  embedding failures are retried up to INGESTION_MAX_ATTEMPTS.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import EmbeddingError, IngestionError, NotFoundError, StorageError
from app.db.base import utcnow
from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.loaders.registry import get_loader
from app.ingestion.normalize import extract_effective_date
from app.ingestion.types import Chunk, ExtractedDocument
from app.models import ChunkEmbedding, Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentStatus, SourceType, VersionStatus
from app.rag.embeddings.base import EmbeddingProvider, embedding_input
from app.security.prompt_injection import detect_injection
from app.storage.base import ObjectStorage
from app.utils.hashing import sha256_bytes, sha256_text

log = structlog.get_logger(__name__)

_CLAIM_SQL = text(
    """
    UPDATE document_versions
       SET status = 'PROCESSING', attempts = attempts + 1, processing_started_at = now(),
           error_code = NULL, error_message = NULL
     WHERE id = (
        SELECT id FROM document_versions
         WHERE attempts < :max_attempts
           AND (status = 'UPLOADED'
                OR (status = 'PROCESSING' AND processing_started_at < now() - make_interval(secs => :stale)))
         ORDER BY created_at
         FOR UPDATE SKIP LOCKED
         LIMIT 1)
    RETURNING id
    """
)
_EXPIRE_SQL = text(
    """
    UPDATE document_versions
       SET status = 'FAILED', error_code = 'MAX_ATTEMPTS',
           error_message = 'Processing did not complete after the maximum number of attempts.'
     WHERE attempts >= :max_attempts
       AND (status = 'UPLOADED'
            OR (status = 'PROCESSING' AND processing_started_at < now() - make_interval(secs => :stale)))
    """
)


@dataclass
class _Prepared:
    extracted: ExtractedDocument
    chunks: list[Chunk]
    vectors: list[list[float]]


class IngestionPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        embedder: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._embedder = embedder
        self._settings = settings
        self._chunker = StructureAwareChunker(
            settings.chunk_target_tokens, settings.chunk_max_tokens, settings.chunk_overlap_tokens
        )

    async def claim_next(self) -> uuid.UUID | None:
        params = {
            "max_attempts": self._settings.ingestion_max_attempts,
            "stale": self._settings.ingestion_stale_after_seconds,
        }
        async with self._sessions() as session, session.begin():
            await session.execute(_EXPIRE_SQL, params)
            row = (await session.execute(_CLAIM_SQL, params)).first()
            return row[0] if row else None

    async def process_version(self, version_id: uuid.UUID) -> VersionStatus:
        bound = log.bind(document_version_id=str(version_id))
        async with self._sessions() as session:
            version = await session.get(DocumentVersion, version_id)
            if version is None or version.status != VersionStatus.PROCESSING:
                return version.status if version else VersionStatus.FAILED
            document = await session.get(Document, version.document_id)
            assert document is not None
            title = document.title
            source_type = SourceType(document.source_type)
            attempts = version.attempts
            storage_key, checksum, filename = version.storage_key, version.checksum, version.filename

        try:
            prepared = await self._prepare(storage_key, checksum, filename, source_type, title)
        except EmbeddingError as exc:
            retry = exc.transient and attempts < self._settings.ingestion_max_attempts
            bound.warning("ingestion_embedding_failed", retry=retry, error=exc.detail)
            await self._set_status(
                version_id,
                VersionStatus.UPLOADED if retry else VersionStatus.FAILED,
                "EMBEDDING_ERROR",
                "Embedding generation failed." + (" It will be retried." if retry else ""),
            )
            return VersionStatus.UPLOADED if retry else VersionStatus.FAILED
        except IngestionError as exc:
            bound.warning("ingestion_failed", error_code=exc.error_code, error=exc.message)
            await self._set_status(version_id, VersionStatus.FAILED, exc.error_code, exc.message)
            return VersionStatus.FAILED
        except (StorageError, NotFoundError) as exc:
            bound.error("ingestion_storage_failed", error=exc.detail or exc.message)
            await self._set_status(
                version_id, VersionStatus.FAILED, "STORAGE_ERROR", "The stored file could not be read."
            )
            return VersionStatus.FAILED
        except Exception as exc:
            bound.exception("ingestion_unexpected_error")
            await self._set_status(
                version_id,
                VersionStatus.FAILED,
                "INTERNAL_ERROR",
                f"Unexpected processing error ({type(exc).__name__}).",
            )
            return VersionStatus.FAILED

        status = await self._persist(version_id, prepared)
        bound.info("ingestion_complete", status=status.value, chunks=len(prepared.chunks))
        return status

    async def _prepare(
        self, storage_key: str, checksum: str, filename: str, source_type: SourceType, title: str
    ) -> _Prepared:
        data = await self._storage.get(storage_key)
        if sha256_bytes(data) != checksum:
            raise IngestionError("The stored file does not match its checksum.", error_code="CHECKSUM_MISMATCH")
        loader = get_loader(source_type, self._settings)
        extracted = await asyncio.to_thread(loader.load, data, filename)
        if extracted.char_count > self._settings.max_document_chars:
            raise IngestionError(
                f"The document has more than {self._settings.max_document_chars:,} characters of text.",
                error_code="DOCUMENT_TOO_LARGE",
            )
        chunks = self._chunker.chunk(extracted)
        if not chunks or extracted.char_count < 20:
            raise IngestionError(
                "No text could be extracted. Scanned documents need OCR before upload.", error_code="NO_TEXT"
            )
        for chunk in chunks:
            flags = detect_injection(chunk.content)
            if flags:
                chunk.metadata["injection_flags"] = flags
        vectors = await self._embedder.embed_documents(
            [embedding_input(title, c.heading_path, c.content) for c in chunks]
        )
        if len(vectors) != len(chunks):
            raise EmbeddingError(detail="Embedding count does not match chunk count", transient=False)
        return _Prepared(extracted, chunks, vectors)

    async def _persist(self, version_id: uuid.UUID, prepared: _Prepared) -> VersionStatus:
        chunks, extracted = prepared.chunks, prepared.extracted
        async with self._sessions() as session, session.begin():
            version = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
            ).scalar_one_or_none()
            if version is None or version.status != VersionStatus.PROCESSING:
                return VersionStatus(version.status) if version else VersionStatus.FAILED
            document = (
                await session.execute(select(Document).where(Document.id == version.document_id).with_for_update())
            ).scalar_one()

            await session.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version_id))
            chunk_rows = []
            embedding_rows = []
            for chunk, vector in zip(chunks, prepared.vectors, strict=True):
                chunk_id = uuid.uuid4()
                chunk_rows.append({
                    "id": chunk_id, "company_id": version.company_id, "document_id": version.document_id,
                    "document_version_id": version_id, "chunk_index": chunk.index, "content": chunk.content,
                    "content_hash": sha256_text(chunk.content), "section_title": chunk.section_title,
                    "heading_path": chunk.heading_path, "page_number": chunk.page_number, "page_end": chunk.page_end,
                    "token_count": chunk.token_count, "metadata_": chunk.metadata,
                })  # fmt: skip
                embedding_rows.append({
                    "id": uuid.uuid4(), "company_id": version.company_id, "chunk_id": chunk_id,
                    "document_version_id": version_id, "embedding_model": self._embedder.model,
                    "dimension": self._embedder.dimension, "embedding": vector,
                })  # fmt: skip
            await session.execute(insert(DocumentChunk), chunk_rows)
            await session.execute(insert(ChunkEmbedding), embedding_rows)

            # Validation: everything that should be indexed is indexed.
            stored_chunks = await session.scalar(
                select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
            )
            stored_vectors = await session.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .where(
                    ChunkEmbedding.document_version_id == version_id,
                    ChunkEmbedding.embedding_model == self._embedder.model,
                )
            )
            if stored_chunks != len(chunks) or stored_vectors != len(chunks):
                raise RuntimeError("Index validation failed: stored rows do not match prepared chunks")

            full_text = "\n".join(b.text for b in extracted.blocks)
            version.status = VersionStatus.READY
            version.chunk_count = len(chunks)
            version.page_count = extracted.page_count
            version.char_count = extracted.char_count
            version.extracted_title = (extracted.title or "")[:300] or None
            version.effective_date = version.effective_date or extract_effective_date(full_text)
            version.embedding_model = self._embedder.model
            version.embedding_dimension = self._embedder.dimension
            version.processed_at = utcnow()
            if extracted.metadata.get("source_uri") and not document.source_uri:
                document.source_uri = str(extracted.metadata["source_uri"])[:1000]

            active_number = 0
            if document.active_version_id:
                active_number = (
                    await session.scalar(
                        select(DocumentVersion.version_number).where(DocumentVersion.id == document.active_version_id)
                    )
                    or 0
                )
            if document.status == DocumentStatus.DELETED or active_number > version.version_number:
                # A newer version is already live (or the document was deleted): keep this one out of retrieval.
                version.status = VersionStatus.SUPERSEDED
            else:
                if document.active_version_id and document.active_version_id != version_id:
                    await session.execute(
                        update(DocumentVersion)
                        .where(DocumentVersion.id == document.active_version_id)
                        .values(status=VersionStatus.SUPERSEDED)
                    )
                document.active_version_id = version_id
                document.updated_at = utcnow()
            return VersionStatus(version.status)

    async def _set_status(self, version_id: uuid.UUID, status: VersionStatus, code: str, message: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id == version_id, DocumentVersion.status == VersionStatus.PROCESSING)
                .values(status=status, error_code=code, error_message=message[:1000], processed_at=utcnow())
            )
