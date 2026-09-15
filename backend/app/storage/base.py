"""Object storage abstraction. PostgreSQL stores only metadata and object keys."""

from __future__ import annotations

import re
import uuid
from typing import Protocol

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ObjectStorage(Protocol):
    name: str

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def healthcheck(self) -> bool: ...


def safe_filename(filename: str, max_length: int = 120) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE.sub("_", base).strip("._") or "document"
    return cleaned[-max_length:]


def build_object_key(company_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID, filename: str) -> str:
    return f"{company_id}/{document_id}/{version_id}/{safe_filename(filename)}"
