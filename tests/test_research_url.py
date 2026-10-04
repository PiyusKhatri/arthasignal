from __future__ import annotations

import pytest

from src.database import holdout_guard
from src.database.holdout_guard import RESEARCH_ROLE, HoldoutQueryViolation, research_url

APP = "postgresql+psycopg2://arthasignal:s3cr3t-app-pw@db.internal:5433/arthasignal?sslmode=require"


def test_research_url_has_no_password_and_never_the_app_password(monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_DATABASE_URL", raising=False)
    url = research_url(APP)
    assert url.username == RESEARCH_ROLE
    assert url.password is None
    rendered = url.render_as_string(hide_password=False)
    assert "s3cr3t-app-pw" not in rendered and ":@" not in rendered
    assert (url.drivername, url.host, url.port, url.database, dict(url.query)) == (
        "postgresql+psycopg2", "db.internal", 5433, "arthasignal", {"sslmode": "require"})


def test_old_set_call_kept_the_app_password() -> None:
    from sqlalchemy.engine import make_url

    assert make_url(APP).set(username=RESEARCH_ROLE, password=None).password == "s3cr3t-app-pw"


def test_research_engine_url_from_settings_has_no_password(monkeypatch) -> None:
    from src.config import settings
    from sqlalchemy.engine import make_url

    monkeypatch.delenv("RESEARCH_DATABASE_URL", raising=False)
    monkeypatch.setattr(holdout_guard, "_research_engine", None)
    engine = holdout_guard.research_engine()
    try:
        assert engine.url.username == RESEARCH_ROLE
        assert engine.url.password is None
        app_password = make_url(settings.database_url).password
        if app_password:
            assert app_password not in engine.url.render_as_string(hide_password=False)
    finally:
        engine.dispose()
        monkeypatch.setattr(holdout_guard, "_research_engine", None)


def test_override_may_carry_a_password_but_only_for_the_research_role(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_DATABASE_URL", f"postgresql+psycopg2://{RESEARCH_ROLE}:rpw@localhost/arthasignal")
    assert research_url(APP).password == "rpw"
    monkeypatch.setenv("RESEARCH_DATABASE_URL", "postgresql+psycopg2://arthasignal:apppw@localhost/arthasignal")
    with pytest.raises(HoldoutQueryViolation):
        research_url(APP)
    monkeypatch.setenv("RESEARCH_DATABASE_URL", f"postgresql+psycopg2://{RESEARCH_ROLE}@db.abc.supabase.co/postgres")
    with pytest.raises(Exception, match="Supabase"):
        research_url(APP)
