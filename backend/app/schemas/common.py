from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    fields: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "The requested resource was not found.",
                    "request_id": "5f0c2a3e9b1d4c7a8e6f0a1b2c3d4e5f",
                }
            }
        }
    )


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        return cls(items=items, total=total, page=page, page_size=page_size, pages=max(1, math.ceil(total / page_size)))


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "Bad request",
        401: "Not authenticated or session expired",
        403: "Authenticated but not allowed",
        404: "Resource not found (or not visible to this user)",
        409: "Conflicts with the current state",
        413: "Upload too large",
        415: "Unsupported file type",
        422: "Validation error",
        429: "Rate limited (see Retry-After header)",
        503: "A dependency (database, LLM, knowledge base) is unavailable",
    }
    return {code: {"model": ErrorResponse, "description": descriptions[code]} for code in codes}
