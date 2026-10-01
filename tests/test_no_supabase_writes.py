from __future__ import annotations

from pathlib import Path

import pytest

from src.config import ConfigError, assert_allowed_database_url

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres.abc:pw@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres",
        "postgresql+psycopg2://postgres:pw@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql://u:p@SUPABASE.COM/postgres",
    ],
)
def test_supabase_hosts_are_refused(url: str) -> None:
    with pytest.raises(ConfigError):
        assert_allowed_database_url("DATABASE_URL", url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://arpanabhandari@localhost:5432/arthasignal",
        "postgresql+psycopg2://artha:pw@127.0.0.1:5432/artha",
        "postgresql://u:p@notsupabase.company.com/db",
    ],
)
def test_other_hosts_are_allowed(url: str) -> None:
    assert assert_allowed_database_url("DATABASE_URL", url) == url


def test_get_settings_refuses_supabase(monkeypatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/arthasignal")
    monkeypatch.setenv("DATABASE_URL_READONLY", "postgresql://u:p@x.pooler.supabase.com:6543/postgres")
    with pytest.raises(ConfigError):
        get_settings()


def test_no_code_or_workflow_reads_a_supabase_variable() -> None:
    paths = list((ROOT / "src").rglob("*.py")) + list((ROOT / ".github" / "workflows").glob("*.yml"))
    offenders = [str(p) for p in paths if "SUPABASE" in p.read_text()]
    assert offenders == []


def test_workflows_refuse_supabase_before_running() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text()
        if "secrets.DATABASE_URL" in text:
            assert 'host.endswith("supabase.com")' in text, path.name
