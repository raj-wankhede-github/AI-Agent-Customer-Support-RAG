"""Centralized error handling. Customers get a classified code and a safe message; logs
get the detail. Stack traces never reach a response."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, DatabaseError, ErrorCode, RateLimitedError

log = structlog.get_logger("errors")

_HTTP_CODES = {
    401: ErrorCode.AUTHENTICATION_ERROR,
    403: ErrorCode.AUTHORIZATION_ERROR,
    404: ErrorCode.NOT_FOUND,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    429: ErrorCode.RATE_LIMITED,
}


def _response(request: Request, status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    headers = extra.pop("headers", None)
    body: dict[str, Any] = {
        "error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}
    }
    return JSONResponse(body, status_code=status, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code.value, detail=exc.detail, path=request.url.path)
        else:
            log.info("client_error", code=exc.code.value, status=exc.status_code, path=request.url.path)
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None
        return _response(request, exc.status_code, exc.code.value, exc.message, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Echo field locations and messages only - never the submitted values.
        fields = [
            {"loc": [str(p) for p in e.get("loc", ())], "message": e.get("msg", "Invalid value")} for e in exc.errors()
        ]
        return _response(request, 422, ErrorCode.VALIDATION_ERROR.value, "The request is invalid.", fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(
            exc.status_code, ErrorCode.VALIDATION_ERROR if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR
        )
        message = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 else "Request failed."
        return _response(request, exc.status_code, code.value, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(ConnectionError)
    @app.exception_handler(OSError)
    async def database_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("database_unavailable", error=type(exc).__name__, path=request.url.path)
        return _response(request, 503, ErrorCode.DATABASE_ERROR.value, DatabaseError.default_message)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return _response(request, 500, ErrorCode.INTERNAL_ERROR.value, AppError.default_message)
