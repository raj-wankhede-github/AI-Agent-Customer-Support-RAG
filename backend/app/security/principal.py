from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.models.enums import UserRole


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Role comes from the database on every request, so a
    demoted or deactivated user loses access immediately."""

    user_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    role: UserRole
    email: str
    name: str
    jti: str
    expires_at: datetime

    @property
    def is_staff(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.AGENT)
