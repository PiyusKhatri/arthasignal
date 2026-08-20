from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from src.api.security import decode_token
from src.database.auth_models import UserAuthState
from src.database.connection import get_session
from src.database.models import User

bearer_scheme = HTTPBearer()


def _token_issued_at(payload: dict) -> datetime:
    issued_ms = payload.get("iat_ms")
    if isinstance(issued_ms, (int, float)):
        return datetime.fromtimestamp(float(issued_ms) / 1000.0, tz=timezone.utc).replace(tzinfo=None)

    issued_at = payload.get("iat")
    if isinstance(issued_at, (int, float)):
        return datetime.fromtimestamp(float(issued_at), tz=timezone.utc).replace(tzinfo=None)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> User:
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    issued_at = _token_issued_at(payload)

    with get_session() as session:
        user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        auth_state = session.get(UserAuthState, user_id)
        if user is not None:
            session.expunge(user)

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    if auth_state is not None and issued_at < auth_state.valid_after:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    return user
