from __future__ import annotations

from app.core.config import Settings
from app.storage.base import ObjectStorage


def create_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_provider == "s3":
        from app.storage.s3 import S3ObjectStorage

        return S3ObjectStorage(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            region=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url,
        )
    from app.storage.local import LocalObjectStorage

    return LocalObjectStorage(settings.storage_local_path)
