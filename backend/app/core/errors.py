"""Classified application errors.

`message` is always safe to show a customer; `detail` is for logs only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    LLM_ERROR = "LLM_ERROR"
    EMBEDDING_ERROR = "EMBEDDING_ERROR"
    RETRIEVAL_ERROR = "RETRIEVAL_ERROR"
    INGESTION_ERROR = "INGESTION_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    default_message = "An unexpected error occurred. Please try again."

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.detail = detail
        self.extra = extra or {}
        super().__init__(detail or self.message)


class ValidationAppError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    status_code = 422
    default_message = "The request is invalid."


class AuthenticationError(AppError):
    code = ErrorCode.AUTHENTICATION_ERROR
    status_code = 401
    default_message = "Authentication is required."


class AuthorizationError(AppError):
    code = ErrorCode.AUTHORIZATION_ERROR
    status_code = 403
    default_message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = 404
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    code = ErrorCode.CONFLICT
    status_code = 409
    default_message = "The request conflicts with the current state of the resource."


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED
    status_code = 429
    default_message = "Too many requests. Please wait a moment and try again."

    def __init__(self, retry_after: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.retry_after = retry_after


class PayloadTooLargeError(AppError):
    code = ErrorCode.PAYLOAD_TOO_LARGE
    status_code = 413
    default_message = "The uploaded file is too large."


class UnsupportedMediaTypeError(AppError):
    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    status_code = 415
    default_message = "This file type is not supported."


class LLMError(AppError):
    code = ErrorCode.LLM_ERROR
    status_code = 503
    default_message = (
        "The AI support service is temporarily unavailable. Please try again or contact a support representative."
    )

    def __init__(self, *args: Any, transient: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.transient = transient


class LLMOutputError(LLMError):
    """The model returned output that does not satisfy the required schema."""


class LLMRefusalError(LLMError):
    """The model declined the request."""


class EmbeddingError(AppError):
    code = ErrorCode.EMBEDDING_ERROR
    status_code = 503
    default_message = "The embedding service is temporarily unavailable."

    def __init__(self, *args: Any, transient: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.transient = transient


class RetrievalError(AppError):
    code = ErrorCode.RETRIEVAL_ERROR
    status_code = 503
    default_message = "The knowledge base is temporarily unavailable."


class IngestionError(AppError):
    code = ErrorCode.INGESTION_ERROR
    status_code = 422
    default_message = "The document could not be processed."

    def __init__(self, message: str, *, error_code: str, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.error_code = error_code


class StorageError(AppError):
    code = ErrorCode.STORAGE_ERROR
    status_code = 503
    default_message = "Document storage is temporarily unavailable."


class DatabaseError(AppError):
    code = ErrorCode.DATABASE_ERROR
    status_code = 503
    default_message = "The service is temporarily unavailable. Your last action was not saved."
