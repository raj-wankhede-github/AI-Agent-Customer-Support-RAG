from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)

    model_config = ConfigDict(json_schema_extra={"example": {"email": "customer@acme.example", "password": "********"}})


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: UserRole
    company_id: uuid.UUID
    company_name: str


class LoginResponse(BaseModel):
    access_token: str = Field(
        description="JWT for API clients (Authorization: Bearer). Browsers use the HttpOnly cookie."
    )
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_at: datetime
    user: UserOut
