"""JWT access tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.models.enums import UserRole


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    company_id: uuid.UUID
    role: UserRole
    jti: str
    expires_at: datetime


def create_access_token(
    settings: Settings, *, user_id: uuid.UUID, company_id: uuid.UUID, role: UserRole
) -> tuple[str, TokenClaims]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.jwt_expires_minutes)
    claims = TokenClaims(user_id, company_id, role, uuid.uuid4().hex, expires)
    payload = {
        "sub": str(user_id),
        "cid": str(company_id),
        "role": role.value,
        "jti": claims.jti,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": settings.jwt_issuer,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)
    return token, claims


def decode_access_token(settings: Settings, token: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "cid", "role", "jti", "exp", "iss"]},
        )
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            company_id=uuid.UUID(payload["cid"]),
            role=UserRole(payload["role"]),
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired. Please sign in again.") from exc
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise AuthenticationError("Invalid authentication credentials.") from exc
