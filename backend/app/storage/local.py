"""Filesystem storage for local development and single-node deployments."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.errors import NotFoundError, StorageError


class LocalObjectStorage:
    name = "local"

    def __init__(self, base_path: str) -> None:
        self.base = Path(base_path).resolve()

    def _path(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if not path.is_relative_to(self.base):
            raise StorageError(detail=f"Object key escapes storage root: {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic on the same filesystem

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            raise StorageError(detail=str(exc)) from exc

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFoundError("The stored document file is missing.") from exc
        except OSError as exc:
            raise StorageError(detail=str(exc)) from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def healthcheck(self) -> bool:
        def _check() -> bool:
            self.base.mkdir(parents=True, exist_ok=True)
            probe = self.base / ".healthcheck"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True

        try:
            return await asyncio.to_thread(_check)
        except OSError:
            return False
