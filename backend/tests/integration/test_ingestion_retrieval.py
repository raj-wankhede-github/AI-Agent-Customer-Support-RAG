"""Ingestion, versioning, pgvector/full-text retrieval and tenant isolation against PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.container import Container
from app.models import ChunkEmbedding, Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentStatus, SourceAuthority, VersionStatus
from app.rag.embeddings.hashing import HashingEmbeddingProvider
from app.rag.retriever import PgHybridRetriever
from app.rag.types import RetrievalFilters
from app.schemas.knowledge import DocumentUpdate
from app.services.knowledge import KnowledgeService, UploadMetadata
from tests.integration.conftest import Tenant, create_tenant, ingest

pytestmark = pytest.mark.integration
SEED = Path(__file__).resolve().parents[2] / "seed" / "acme"


async def upload(container: Container, tenant: Tenant, filename: str, data: bytes, **meta) -> tuple[str, str]:
    service = KnowledgeService(container.settings, container.storage)
    async with container.sessions() as session:
        result = await service.upload(
            session,
            tenant.principal(tenant.admin),
            filename=filename,
            content_type=None,
            data=data,
            meta=UploadMetadata(**meta),
        )
    return str(result.document.id), str(result.version.id)


async def candidates(container: Container, tenant: Tenant, query: str, retriever=None):
    result = await (retriever or container.retriever).retrieve(
        company_id=tenant.company.id, query=query, filters=RetrievalFilters()
    )
    return result.candidates


async def test_pdf_is_ingested_indexed_and_retrievable(container: Container, acme: Tenant):
    document_id, version_id = await upload(
        container,
        acme,
        "shipping-policy.pdf",
        (SEED / "shipping-policy.pdf").read_bytes(),
        authority=SourceAuthority.OFFICIAL_POLICY,
    )
    assert await ingest(container) == 1
    async with container.sessions() as session:
        version = await session.get(DocumentVersion, __import__("uuid").UUID(version_id))
        document = await session.get(Document, version.document_id)
        chunks = await session.scalar(select(func.count()).where(DocumentChunk.document_version_id == version.id))
        vectors = await session.scalar(select(func.count()).where(ChunkEmbedding.document_version_id == version.id))
    assert version.status == VersionStatus.READY and version.page_count == 2
    assert str(version.effective_date) == "2026-01-01"  # extracted from the document text
    assert document.active_version_id == version.id and str(document.id) == document_id
    assert chunks == vectors == version.chunk_count > 0

    found = await candidates(container, acme, "What is your standard shipping time?")
    assert found and found[0].section_title == "Standard Shipping" and found[0].page_number == 1
    assert found[0].vector_rank is not None and found[0].lexical_rank is not None


async def test_failed_ingestion_is_recorded_and_never_searchable(container: Container, acme: Tenant):
    _, version_id = await upload(container, acme, "broken.pdf", b"%PDF-1.4\nnot actually a pdf body")
    await ingest(container)
    async with container.sessions() as session:
        version = await session.get(DocumentVersion, __import__("uuid").UUID(version_id))
        document = await session.get(Document, version.document_id)
    assert version.status == VersionStatus.FAILED and version.error_code == "CORRUPT_FILE" and version.error_message
    assert document.active_version_id is None
    assert await candidates(container, acme, "pdf body") == []


async def test_new_version_goes_live_only_after_processing(container: Container, acme: Tenant):
    v1 = b"# Returns\n\n## Return Window\n\nItems can be returned within 30 days of delivery.\n"
    v2 = b"# Returns\n\n## Return Window\n\nItems can be returned within 14 days of delivery.\n"
    document_id, first_version = await upload(container, acme, "returns.md", v1)
    await ingest(container)
    _, second_version = await upload(
        container, acme, "returns.md", v2, document_id=__import__("uuid").UUID(document_id)
    )

    before = await candidates(container, acme, "return window days")
    assert before and "30 days" in before[0].content  # old version still serving while v2 is queued

    await ingest(container)
    after = await candidates(container, acme, "return window days")
    assert after and "14 days" in after[0].content and all("30 days" not in c.content for c in after)
    async with container.sessions() as session:
        old = await session.get(DocumentVersion, __import__("uuid").UUID(first_version))
        new = await session.get(DocumentVersion, __import__("uuid").UUID(second_version))
    assert (old.status, new.status, new.version_number) == (VersionStatus.SUPERSEDED, VersionStatus.READY, 2)


async def test_identical_upload_is_deduplicated(container: Container, acme: Tenant):
    data = (SEED / "refund-policy.md").read_bytes()
    service = KnowledgeService(container.settings, container.storage)
    async with container.sessions() as session:
        first = await service.upload(
            session,
            acme.principal(acme.admin),
            filename="refund.md",
            content_type=None,
            data=data,
            meta=UploadMetadata(),
        )
    async with container.sessions() as session:
        second = await service.upload(
            session,
            acme.principal(acme.admin),
            filename="refund-copy.md",
            content_type=None,
            data=data,
            meta=UploadMetadata(),
        )
        versions = await session.scalar(select(func.count()).select_from(DocumentVersion))
    assert not first.duplicate and second.duplicate and second.document.id == first.document.id and versions == 1


async def test_tenants_never_see_each_others_knowledge(container: Container, acme: Tenant):
    globex = await create_tenant(container, "globex")
    await upload(
        container,
        acme,
        "acme.md",
        b"# Shipping\n\n## Standard Shipping\n\nAcme standard shipping takes 3-5 business days.\n",
    )
    await upload(
        container,
        globex,
        "globex.md",
        b"# Shipping\n\n## Standard Shipping\n\nGlobex standard shipping takes 9-12 business days.\n",
    )
    await ingest(container)
    acme_hits = await candidates(container, acme, "standard shipping business days")
    globex_hits = await candidates(container, globex, "standard shipping business days")
    assert acme_hits and all("Globex" not in c.content for c in acme_hits)
    assert globex_hits and all("Acme" not in c.content for c in globex_hits)


async def test_inactive_and_deleted_documents_are_excluded(container: Container, acme: Tenant):
    document_id, _ = await upload(
        container, acme, "faq.md", b"# FAQ\n\n## Gift Cards\n\nGift cards never expire and have no fees.\n"
    )
    await ingest(container)
    assert await candidates(container, acme, "gift cards expire")
    service = KnowledgeService(container.settings, container.storage)
    doc_uuid = __import__("uuid").UUID(document_id)
    async with container.sessions() as session:
        await service.update(
            session, acme.principal(acme.admin), doc_uuid, DocumentUpdate(status=DocumentStatus.INACTIVE)
        )
    assert await candidates(container, acme, "gift cards expire") == []
    async with container.sessions() as session:
        await service.update(
            session, acme.principal(acme.admin), doc_uuid, DocumentUpdate(status=DocumentStatus.ACTIVE)
        )
        await service.delete(session, acme.principal(acme.admin), doc_uuid)
    assert await candidates(container, acme, "gift cards expire") == []


async def test_vectors_from_a_different_embedding_model_are_never_used(container: Container, acme: Tenant):
    await upload(container, acme, "faq.md", b"# FAQ\n\n## Gift Cards\n\nGift cards never expire and have no fees.\n")
    await ingest(container)
    other_model = PgHybridRetriever(
        container.sessions, HashingEmbeddingProvider(384, model="hashing-v2"), container.settings
    )
    assert await candidates(container, acme, "gift cards expire", retriever=other_model) == []


async def test_reindex_builds_a_new_version_while_the_current_one_serves(container: Container, acme: Tenant):
    document_id, _ = await upload(
        container, acme, "faq.md", b"# FAQ\n\n## Gift Cards\n\nGift cards never expire and have no fees.\n"
    )
    await ingest(container)
    service = KnowledgeService(container.settings, container.storage)
    async with container.sessions() as session:
        version = await service.reindex(
            session, acme.principal(acme.admin), __import__("uuid").UUID(document_id), acme.company.id
        )
    assert version.version_number == 2 and version.status == VersionStatus.UPLOADED
    assert await candidates(container, acme, "gift cards expire")  # v1 still live
    await ingest(container)
    async with container.sessions() as session:
        document = await session.get(Document, __import__("uuid").UUID(document_id))
    assert document.active_version_id == version.id
