from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentStatus, SourceAuthority, SourceType, VersionStatus


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_number: int
    filename: str
    mime_type: str
    size_bytes: int
    checksum: str
    status: VersionStatus
    error_code: str | None
    error_message: str | None
    attempts: int
    embedding_model: str | None
    chunk_count: int
    page_count: int | None
    extracted_title: str | None
    effective_date: date | None
    created_at: datetime
    processed_at: datetime | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    source_type: SourceType
    source_uri: str | None
    authority: SourceAuthority
    category: str | None
    product: str | None
    locale: str | None
    effective_date: date | None
    status: DocumentStatus
    processing_status: str = Field(
        description="UPLOADED | PROCESSING | READY | FAILED | INACTIVE | DELETED - what an admin needs to see at a glance"
    )
    searchable: bool = Field(description="True when an active, fully indexed version is used by retrieval")
    active_version_number: int | None
    latest_version: VersionOut | None
    created_at: datetime
    updated_at: datetime


class ChunkPreview(BaseModel):
    chunk_index: int
    section_title: str | None
    heading_path: str | None
    page_number: int | None
    token_count: int
    content: str
    metadata: dict[str, Any]


class DocumentDetail(BaseModel):
    document: DocumentOut
    versions: list[VersionOut]
    chunks: list[ChunkPreview]


class UploadResponse(BaseModel):
    document: DocumentOut
    version: VersionOut
    duplicate: bool = Field(description="True when identical content already exists; nothing new was queued")
    message: str


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    authority: SourceAuthority | None = None
    category: str | None = Field(default=None, max_length=100)
    product: str | None = Field(default=None, max_length=100)
    locale: str | None = Field(default=None, max_length=20)
    effective_date: date | None = None
    source_uri: str | None = Field(default=None, max_length=1000)
    status: DocumentStatus | None = Field(default=None, description="ACTIVE or INACTIVE (use DELETE to delete)")
