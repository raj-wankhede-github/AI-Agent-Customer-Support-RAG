"""FastAPI dependencies: container, DB session, authentication, RBAC, CSRF, rate limits."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Literal

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.container import Container
from app.core.errors import AuthenticationError, AuthorizationError, RateLimitedError
from app.models.enums import UserRole
from app.security.principal import Principal
from app.security.rate_limit import RateLimit
from app.services.auth import resolve_principal

_bearer = HTTPBearer(auto_error=False, description="JWT from POST /api/auth/login (browsers use the session cookie)")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_settings_dep(container: Annotated[Container, Depends(get_container)]) -> Settings:
    return container.settings


async def get_session(container: Annotated[Container, Depends(get_container)]) -> AsyncIterator[AsyncSession]:
    async with container.sessions() as session:
        yield session


async def get_principal(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    settings = container.settings
    token = credentials.credentials if credentials else request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise AuthenticationError()
    # Cookie-authenticated writes must carry a custom header. Browsers cannot add it
    # cross-site without a CORS preflight, which our CORS policy rejects (CSRF defense).
    if (
        credentials is None
        and request.method not in _SAFE_METHODS
        and request.headers.get("x-requested-with") != "XMLHttpRequest"
    ):
        raise AuthorizationError("Missing X-Requested-With header.")
    principal = await resolve_principal(session, settings, token)
    request.state.principal = principal
    structlog.contextvars.bind_contextvars(user_id=str(principal.user_id), company_id=str(principal.company_id))
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
AppContainer = Annotated[Container, Depends(get_container)]


def require_roles(*roles: UserRole) -> Callable[[Principal], Awaitable[Principal]]:
    async def dependency(principal: CurrentPrincipal) -> Principal:
        if principal.role not in roles:
            raise AuthorizationError()
        return principal

    return dependency


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


Bucket = Literal["login", "chat", "upload", "admin"]


async def _hit(container: Container, bucket: Bucket, identity: str) -> None:
    settings = container.settings
    if not settings.rate_limit_enabled:
        return
    limit = RateLimit.parse(getattr(settings, f"rate_limit_{bucket}"))
    retry_after = await container.rate_limiter.hit(f"{bucket}:{identity}", limit)
    if retry_after is not None:
        raise RateLimitedError(retry_after=retry_after)


def rate_limited(bucket: Bucket, *, per_user: bool = True) -> Callable[..., Awaitable[None]]:
    """Per-user limits for authenticated endpoints; per-client-IP for anonymous ones (login)."""
    if per_user:

        async def user_dependency(container: AppContainer, principal: CurrentPrincipal) -> None:
            await _hit(container, bucket, f"user:{principal.user_id}")

        return user_dependency

    async def ip_dependency(request: Request, container: AppContainer) -> None:
        await _hit(container, bucket, f"ip:{client_ip(request, container.settings)}")

    return ip_dependency


StaffPrincipal = Annotated[Principal, Depends(require_roles(UserRole.ADMIN, UserRole.AGENT))]
AdminPrincipal = Annotated[Principal, Depends(require_roles(UserRole.ADMIN))]
