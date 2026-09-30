from __future__ import annotations

import hashlib
import uuid
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select

import src.api.auth as auth_module
from src.api.main import app
from src.database.connection import get_session
from src.database.models import PasswordResetToken, User


def test_auth_rotation_reset_hashing_and_revocation(monkeypatch) -> None:
    client = TestClient(app)
    email = f"ci-{uuid.uuid4().hex}@example.com"
    old_password = "OriginalPass123"
    new_password = "ChangedPass456"
    captured: dict[str, str] = {}

    def fake_reset_email(recipient: str, reset_url: str) -> bool:
        assert recipient == email
        captured["url"] = reset_url
        return True

    monkeypatch.setattr(auth_module, "send_password_reset_email", fake_reset_email)

    signup = client.post("/auth/signup", json={"email": email, "password": old_password})
    assert signup.status_code == 201, signup.text

    login = client.post("/auth/login", json={"email": email, "password": old_password})
    assert login.status_code == 200, login.text
    first_tokens = login.json()

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {first_tokens['access_token']}"})
    assert me.status_code == 200, me.text

    refresh = client.post("/auth/refresh", json={"refresh_token": first_tokens["refresh_token"]})
    assert refresh.status_code == 200, refresh.text
    rotated_tokens = refresh.json()
    assert rotated_tokens["refresh_token"] != first_tokens["refresh_token"]

    replay = client.post("/auth/refresh", json={"refresh_token": first_tokens["refresh_token"]})
    assert replay.status_code == 401

    forgot = client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 200, forgot.text
    forgot_payload = forgot.json()
    assert set(forgot_payload) == {"detail"}
    assert "reset_token" not in forgot_payload
    assert "url" in captured

    raw_reset_token = parse_qs(urlparse(captured["url"]).query)["token"][0]
    reset_digest = hashlib.sha256(raw_reset_token.encode("utf-8")).hexdigest()

    with get_session() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one()
        reset_row = session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .order_by(PasswordResetToken.id.desc())
        ).scalars().first()
        assert reset_row is not None
        assert reset_row.token == reset_digest
        assert reset_row.token != raw_reset_token

    reset = client.post(
        "/auth/reset-password",
        json={"token": raw_reset_token, "new_password": new_password},
    )
    assert reset.status_code == 200, reset.text

    revoked_access = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {rotated_tokens['access_token']}"},
    )
    assert revoked_access.status_code == 401

    revoked_refresh = client.post(
        "/auth/refresh",
        json={"refresh_token": rotated_tokens["refresh_token"]},
    )
    assert revoked_refresh.status_code == 401

    old_login = client.post("/auth/login", json={"email": email, "password": old_password})
    assert old_login.status_code == 401

    new_login = client.post("/auth/login", json={"email": email, "password": new_password})
    assert new_login.status_code == 200, new_login.text
