from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.api.dependencies import get_current_user
from src.api.email_service import send_password_reset_email
from src.api.rate_limit import (
    AUTH_LOGIN_RATE_LIMIT,
    AUTH_PASSWORD_RESET_RATE_LIMIT,
    AUTH_REFRESH_RATE_LIMIT,
    AUTH_SIGNUP_RATE_LIMIT,
    limiter,
)
from src.api.security import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from src.config import settings
from src.database.auth_models import RefreshSession
from src.database.connection import get_session
from src.database.models import PasswordResetToken, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72
RESET_TOKEN_EXPIRE_MINUTES = 30


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_password_strength(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must not exceed {MAX_PASSWORD_BYTES} bytes")
    if not re.search(r"[A-Za-z]", value):
        raise ValueError("password must contain at least one letter")
    if not re.search(r"[0-9]", value):
        raise ValueError("password must contain at least one digit")
    return value


class SignupRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    created_at: datetime
    is_active: bool


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    detail: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


def _issue_token_pair(session, user_id: int) -> TokenResponse:
    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)
    now = _utcnow_naive()
    session.add(
        RefreshSession(
            user_id=user_id,
            token_hash=_token_hash(refresh_token),
            expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
            revoked=False,
            created_at=now,
        )
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_SIGNUP_RATE_LIMIT)
def signup(request: Request, payload: SignupRequest) -> UserResponse:
    del request
    email_value = str(payload.email).strip().lower()
    hashed = hash_password(payload.password)

    with get_session() as session:
        existing = session.execute(select(User).where(User.email == email_value)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        user = User(
            email=email_value,
            hashed_password=hashed,
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        user_id, email, created_at, is_active = user.id, user.email, user.created_at, user.is_active

    return UserResponse(id=user_id, email=email, created_at=created_at, is_active=is_active)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(AUTH_LOGIN_RATE_LIMIT)
def login(request: Request, payload: LoginRequest) -> TokenResponse:
    del request
    email_value = str(payload.email).strip().lower()
    with get_session() as session:
        user = session.execute(select(User).where(User.email == email_value)).scalar_one_or_none()
        if user is None or not verify_password(payload.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive")
        return _issue_token_pair(session, user.id)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(AUTH_REFRESH_RATE_LIMIT)
def refresh(request: Request, payload: RefreshRequest) -> TokenResponse:
    del request
    try:
        token_payload = decode_token(payload.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    try:
        user_id = int(token_payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    digest = _token_hash(payload.refresh_token)
    now = _utcnow_naive()
    with get_session() as session:
        refresh_session = session.execute(
            select(RefreshSession)
            .where(RefreshSession.user_id == user_id)
            .where(RefreshSession.token_hash == digest)
            .where(RefreshSession.revoked.is_(False))
        ).scalar_one_or_none()
        if refresh_session is None or refresh_session.expires_at <= now:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is no longer valid")

        user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

        # One-time refresh semantics: rotate and revoke the old bearer token.
        refresh_session.revoked = True
        return _issue_token_pair(session, user.id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(AUTH_REFRESH_RATE_LIMIT)
def logout(request: Request, payload: RefreshRequest) -> Response:
    del request
    with get_session() as session:
        session.execute(
            update(RefreshSession)
            .where(RefreshSession.token_hash == _token_hash(payload.refresh_token))
            .values(revoked=True)
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        created_at=current_user.created_at,
        is_active=current_user.is_active,
    )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit(AUTH_PASSWORD_RESET_RATE_LIMIT)
def forgot_password(request: Request, payload: ForgotPasswordRequest) -> ForgotPasswordResponse:
    del request
    generic_response = ForgotPasswordResponse(
        detail="If that email is registered, password reset instructions have been sent."
    )
    email_value = str(payload.email).strip().lower()

    with get_session() as session:
        user = session.execute(select(User).where(User.email == email_value)).scalar_one_or_none()
        if user is None or not user.is_active:
            return generic_response

        # Invalidate any older links before issuing a new one.
        session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .where(PasswordResetToken.used.is_(False))
            .values(used=True)
        )

        raw_token = secrets.token_urlsafe(32)
        reset_token = PasswordResetToken(
            user_id=user.id,
            # Never persist the bearer token itself.
            token=_token_hash(raw_token),
            expires_at=_utcnow_naive() + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
            used=False,
        )
        session.add(reset_token)
        session.flush()

        reset_url = f"{settings.frontend_base_url}/reset-password?token={quote(raw_token, safe='')}"
        if not send_password_reset_email(user.email, reset_url):
            # Do not leave a usable token behind when delivery did not occur.
            session.rollback()
            return generic_response

    return generic_response


@router.post("/reset-password", response_model=UserResponse)
@limiter.limit(AUTH_PASSWORD_RESET_RATE_LIMIT)
def reset_password(request: Request, payload: ResetPasswordRequest) -> UserResponse:
    del request
    token_digest = _token_hash(payload.token)
    with get_session() as session:
        reset_token = session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token == token_digest)
        ).scalar_one_or_none()

        if reset_token is None or reset_token.used:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or already-used token")
        if reset_token.expires_at <= _utcnow_naive():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token has expired")

        user = session.execute(select(User).where(User.id == reset_token.user_id)).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or already-used token")

        user.hashed_password = hash_password(payload.new_password)
        reset_token.used = True
        session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .where(PasswordResetToken.used.is_(False))
            .values(used=True)
        )
        # Password reset immediately invalidates every long-lived session.
        session.execute(
            update(RefreshSession).where(RefreshSession.user_id == user.id).values(revoked=True)
        )

        session.flush()
        user_id, email, created_at, is_active = user.id, user.email, user.created_at, user.is_active

    return UserResponse(id=user_id, email=email, created_at=created_at, is_active=is_active)
