from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models import Base


class RefreshSession(Base):
    """Server-side record for a refresh token.

    Only a SHA-256 digest of the bearer token is stored. Refresh tokens are
    rotated on every successful refresh and can be revoked independently of
    short-lived access tokens.
    """

    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UserAuthState(Base):
    """Per-user access-token revocation watermark.

    Access tokens issued before valid_after are rejected. This allows password
    resets and other security events to invalidate already-issued short-lived
    access tokens without changing the legacy users table.
    """

    __tablename__ = "user_auth_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    valid_after: Mapped[datetime] = mapped_column(DateTime, nullable=False)
