from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import AppContainer, CurrentPrincipal, DbSession, rate_limited
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.schemas.common import error_responses
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Sign in",
    description=(
        "Verifies email and password and starts a session. Sets an HttpOnly, SameSite=Lax session cookie for "
        "browsers and also returns the JWT for API clients (`Authorization: Bearer <token>`). Rate limited per IP."
    ),
    responses=error_responses(401, 422, 429),
    dependencies=[Depends(rate_limited("login", per_user=False))],
)
async def login(body: LoginRequest, response: Response, session: DbSession, container: AppContainer) -> LoginResponse:
    settings = container.settings
    result, token = await auth_service.login(session, settings, body.email, body.password)
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expires_minutes * 60,
        path="/",
    )
    return result


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out",
    description="Revokes the current token server-side and clears the session cookie.",
    responses=error_responses(401),
)
async def logout(
    principal: CurrentPrincipal, response: Response, session: DbSession, container: AppContainer
) -> Response:
    await auth_service.logout(session, principal)
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(container.settings.auth_cookie_name, path="/")
    return response


@router.get("/me", response_model=UserOut, summary="Current user", responses=error_responses(401))
async def me(principal: CurrentPrincipal) -> UserOut:
    return auth_service.user_out(principal)
