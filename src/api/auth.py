from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.api.dependencies import get_current_user
from src.api.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from src.database.connection import get_session
from src.database.models import PasswordResetToken, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72
RESET_TOKEN_EXPIRE_MINUTES = 30


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
    reset_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest) -> UserResponse:
    hashed = hash_password(payload.password)

    with get_session() as session:
        existing = session.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        user = User(
            email=payload.email,
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
def login(payload: LoginRequest) -> TokenResponse:
    with get_session() as session:
        user = session.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
        if user is not None:
            session.expunge(user)

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest) -> TokenResponse:
    try:
        token_payload = decode_token(payload.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id_raw = token_payload.get("sub")
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    with get_session() as session:
        user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if user is not None:
            session.expunge(user)

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        created_at=current_user.created_at,
        is_active=current_user.is_active,
    )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest) -> ForgotPasswordResponse:
    generic_response = ForgotPasswordResponse(
        detail="If that email is registered, a password reset token has been issued."
    )

    with get_session() as session:
        user = session.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
        if user is None or not user.is_active:
            return generic_response

        user_id = user.id
        token = secrets.token_urlsafe(32)
        reset_token = PasswordResetToken(
            user_id=user_id,
            token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
            used=False,
        )
        session.add(reset_token)

    logger.warning(
        "TEMPORARY TESTING SHORTCUT - no email delivery is wired up yet, returning reset token "
        "directly in the API response. This must be replaced with real email delivery before launch. "
        "token=%s user_id=%s",
        token,
        user_id,
    )

    return ForgotPasswordResponse(detail=generic_response.detail, reset_token=token)


@router.post("/reset-password", response_model=UserResponse)
def reset_password(payload: ResetPasswordRequest) -> UserResponse:
    with get_session() as session:
        reset_token = session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token == payload.token)
        ).scalar_one_or_none()

        if reset_token is None or reset_token.used:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or already-used token")

        if reset_token.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token has expired")

        user = session.execute(select(User).where(User.id == reset_token.user_id)).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or already-used token")

        user.hashed_password = hash_password(payload.new_password)
        reset_token.used = True

        session.flush()
        user_id, email, created_at, is_active = user.id, user.email, user.created_at, user.is_active

    return UserResponse(id=user_id, email=email, created_at=created_at, is_active=is_active)
