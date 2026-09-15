"""S3 (or S3-compatible, e.g. MinIO) storage. Credentials come from the standard AWS
credential chain (IAM task role on ECS/Fargate), never from application config."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import NotFoundError, StorageError


class S3ObjectStorage:
    name = "s3"

    def __init__(self, bucket: str, prefix: str, region: str, endpoint_url: str | None) -> None:
        import boto3  # optional dependency: `uv sync --extra s3`

        self.bucket = bucket
        self.prefix = prefix
        self._client: Any = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self.bucket,
                Key=self._key(key),
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            raise StorageError(detail=f"S3 put failed: {type(exc).__name__}") from exc

    async def get(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=self._key(key))
            body: bytes = await asyncio.to_thread(response["Body"].read)
            return body
        except self._client.exceptions.NoSuchKey as exc:
            raise NotFoundError("The stored document file is missing.") from exc
        except Exception as exc:
            raise StorageError(detail=f"S3 get failed: {type(exc).__name__}") from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            raise StorageError(detail=f"S3 delete failed: {type(exc).__name__}") from exc

    async def healthcheck(self) -> bool:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)
            return True
        except Exception:
            return False
