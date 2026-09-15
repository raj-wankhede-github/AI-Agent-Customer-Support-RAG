from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.models import Company, RevokedToken, User
from app.models.enums import UserRole
from app.schemas.auth import LoginResponse, UserOut
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.principal import Principal
from app.security.tokens import TokenClaims, create_access_token, decode_access_token
from app.services.audit import record_audit

_INVALID = "Invalid email or password."


async def login(session: AsyncSession, settings: Settings, email: str, password: str) -> tuple[LoginResponse, str]:
    row = (
        await session.execute(
            select(User, Company.name)
            .join(Company, Company.id == User.company_id)
            .where(func.lower(User.email) == email.lower())
        )
    ).first()
    user, company_name = (row[0], row[1]) if row else (None, "")
    if not verify_password(password, user.password_hash if user else None) or user is None or not user.is_active:
        record_audit(
            session, action="auth.login_failed", company_id=user.company_id if user else None,
            actor_user_id=None, details={"reason": "invalid_credentials"},
        )  # fmt: skip
        await session.commit()
        raise AuthenticationError(_INVALID)

    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    token, claims = create_access_token(settings, user_id=user.id, company_id=user.company_id, role=UserRole(user.role))
    record_audit(
        session,
        action="auth.login",
        company_id=user.company_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
    )
    await session.commit()
    response = LoginResponse(
        access_token=token,
        expires_at=claims.expires_at,
        user=UserOut(id=user.id, email=user.email, name=user.name, role=UserRole(user.role),
                     company_id=user.company_id, company_name=company_name),
    )  # fmt: skip
    return response, token


async def resolve_principal(session: AsyncSession, settings: Settings, token: str) -> Principal:
    claims: TokenClaims = decode_access_token(settings, token)
    if await session.get(RevokedToken, claims.jti) is not None:
        raise AuthenticationError("Your session has ended. Please sign in again.")
    row = (
        await session.execute(
            select(User, Company.name)
            .join(Company, Company.id == User.company_id)
            .where(User.id == claims.user_id, User.company_id == claims.company_id)
        )
    ).first()
    if row is None or not row[0].is_active:
        raise AuthenticationError("Invalid authentication credentials.")
    user, company_name = row
    return Principal(
        user_id=user.id, company_id=user.company_id, company_name=company_name, role=UserRole(user.role),
        email=user.email, name=user.name, jti=claims.jti, expires_at=claims.expires_at,
    )  # fmt: skip


async def logout(session: AsyncSession, principal: Principal) -> None:
    await session.execute(
        insert(RevokedToken).values(jti=principal.jti, expires_at=principal.expires_at).on_conflict_do_nothing()
    )
    await session.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC)))
    record_audit(session, action="auth.logout", company_id=principal.company_id, actor_user_id=principal.user_id)
    await session.commit()


def user_out(principal: Principal) -> UserOut:
    return UserOut(
        id=principal.user_id, email=principal.email, name=principal.name, role=principal.role,
        company_id=principal.company_id, company_name=principal.company_name,
    )  # fmt: skip


async def create_user(
    session: AsyncSession, *, company_id: uuid.UUID, email: str, name: str, role: UserRole, password: str
) -> User:
    user = User(company_id=company_id, email=email.lower(), name=name, role=role, password_hash=hash_password(password))
    session.add(user)
    await session.flush()
    return user
