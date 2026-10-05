from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from src.database import holdout_guard as hg
from src.ops import write_check

ROOT = Path(__file__).resolve().parents[1]
APP_ROLE = "arthasignal_rolecheck_app"
DB_NAME = f"arthasignal_rolecheck_{os.getpid()}"
TABLES = (
    "CREATE TABLE backtest_variant_trials (id serial PRIMARY KEY, model_family varchar(100) NOT NULL, variant_fingerprint varchar(64) NOT NULL, "
    "description text NOT NULL, created_at timestamp NOT NULL, UNIQUE (model_family, variant_fingerprint))",
    "CREATE TABLE backtest_holdout_evaluations (id serial PRIMARY KEY, model_id varchar(200) NOT NULL, summary_json text, requested_at timestamp NOT NULL)",
    "CREATE TABLE daily_prices (id serial PRIMARY KEY, symbol varchar(20) NOT NULL, date date NOT NULL, close numeric NOT NULL, UNIQUE (symbol, date))",
    "CREATE TABLE scorecard_calls (id serial PRIMARY KEY, symbol varchar(20) NOT NULL, signal_date date NOT NULL, mode varchar(10) NOT NULL "
    "CHECK (mode IN ('replay', 'live')), UNIQUE (symbol, signal_date))",
    "CREATE TABLE ops_runs (id serial PRIMARY KEY, step varchar(40) NOT NULL, started_at timestamptz NOT NULL)",
    "ALTER TABLE backtest_variant_trials ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE backtest_holdout_evaluations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ops_runs ENABLE ROW LEVEL SECURITY",
)
TODAY = "(now() AT TIME ZONE 'Asia/Kathmandu')::date"


def _admin_url():
    from src.config import settings

    return make_url(settings.database_url)


@pytest.fixture(scope="module")
def server_like():
    admin_url = _admin_url()
    try:
        admin = create_engine(admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
        with admin.connect() as connection:
            if not connection.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
                pytest.skip("needs a superuser connection to build the server-like database")
            if connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": hg.RESEARCH_ROLE}).scalar() is None:
                pytest.skip("arthasignal_research role is not present")
            connection.execute(text(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
                                    f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$"))
            connection.execute(text(f"DROP DATABASE IF EXISTS {DB_NAME}"))
            connection.execute(text(f"CREATE DATABASE {DB_NAME} OWNER {APP_ROLE}"))
    except DBAPIError as exc:
        pytest.skip(f"database not reachable: {exc}")
    app = create_engine(admin_url.set(username=APP_ROLE, password=None, database=DB_NAME), future=True)
    with app.begin() as connection:
        for statement in TABLES:
            connection.execute(text(statement))
    superuser = create_engine(admin_url.set(database=DB_NAME), future=True)
    grants = (ROOT / "deploy" / "sql" / "role_grants.sql").read_text()
    with superuser.begin() as connection:
        connection.execute(text(f"SET arthasignal.new_owner = '{APP_ROLE}'"))
        connection.connection.cursor().execute(grants)
        connection.execute(text(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {hg.RESEARCH_ROLE}"))
    research = create_engine(admin_url.set(username=hg.RESEARCH_ROLE, password=None, database=DB_NAME), future=True,
                             connect_args={"options": f"-c {hg.GUC}=on"})
    with app.begin() as connection:
        connection.execute(text("INSERT INTO daily_prices (symbol, date, close) VALUES ('AAA', DATE '2025-09-28', 100), ('AAA', "
                                f"{TODAY}, 101)"))
        connection.execute(text("INSERT INTO backtest_holdout_evaluations (model_id, summary_json, requested_at) "
                                "VALUES ('m', 'holdout result', timezone('utc', now()))"))
    yield {"app": app, "research": research}
    for engine in (app, research, superuser):
        engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)"))
    admin.dispose()


def _register_insert(connection, family: str) -> int:
    return connection.execute(text(
        "INSERT INTO backtest_variant_trials (model_family, variant_fingerprint, description, created_at) "
        "VALUES (:f, :f, 'test', timezone('utc', now())) ON CONFLICT (model_family, variant_fingerprint) DO NOTHING"), {"f": family}).rowcount


def test_research_insert_into_registry_is_refused_without_the_registry_policies(server_like) -> None:
    with server_like["research"].connect() as connection:
        with pytest.raises(DBAPIError, match="row-level security"):
            _register_insert(connection, "before-fix")


def test_research_can_register_and_count_today_after_the_policies(server_like) -> None:
    hg.apply_policies(server_like["app"])
    assert hg.apply_research_registry_policies(server_like["app"]) == ["backtest_variant_trials"]
    with server_like["research"].begin() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == hg.RESEARCH_ROLE
        assert _register_insert(connection, "family-a") == 1
        assert _register_insert(connection, "family-a") == 0
        assert connection.execute(text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = 'family-a'")).scalar_one() == 1
        assert connection.execute(text("SELECT created_at::date FROM backtest_variant_trials WHERE model_family = 'family-a'")).scalar_one() \
            >= hg.HOLDOUT_START


def test_research_still_cannot_read_or_write_the_holdout(server_like) -> None:
    hg.apply_policies(server_like["app"])
    hg.apply_research_registry_policies(server_like["app"])
    with server_like["research"].connect() as connection:
        assert str(connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one()) == "2025-09-28"
        assert connection.execute(text("SELECT count(*) FROM backtest_holdout_evaluations")).scalar_one() == 0
        connection.rollback()
        for statement in (
            f"INSERT INTO daily_prices (symbol, date, close) VALUES ('BBB', {TODAY}, 1)",
            f"INSERT INTO scorecard_calls (symbol, signal_date, mode) VALUES ('BBB', {TODAY}, 'live')",
            "INSERT INTO backtest_holdout_evaluations (model_id, requested_at) VALUES ('x', timezone('utc', now()))",
            "UPDATE backtest_variant_trials SET description = 'x'",
            "DELETE FROM backtest_variant_trials",
        ):
            with pytest.raises(DBAPIError, match="row-level security|permission denied"):
                with connection.begin():
                    connection.execute(text(statement))


def test_app_role_inserts_today_dated_rows_into_writer_tables(server_like) -> None:
    hg.apply_policies(server_like["app"])
    hg.apply_research_registry_policies(server_like["app"])
    with server_like["app"].connect() as connection:
        assert connection.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")).scalar_one() is False
        results = {t: write_check.probe(connection, t)["result"]
                   for t in ("daily_prices", "scorecard_calls", "backtest_variant_trials", "ops_runs")}
        connection.rollback()
    assert results == {"daily_prices": "ok", "scorecard_calls": "ok", "backtest_variant_trials": "ok", "ops_runs": "ok"}
    with server_like["app"].connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM daily_prices")).scalar_one() == 2


def test_write_check_research_part_passes_and_rolls_back(server_like) -> None:
    hg.apply_policies(server_like["app"])
    hg.apply_research_registry_policies(server_like["app"])
    with server_like["research"].connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM backtest_variant_trials")).scalar_one()
    result = write_check.check_research(server_like["research"])
    assert result["ok"], result
    assert result["must_stay_refused"]["backtest_holdout_evaluations"] == "REFUSED"
    with server_like["research"].connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM backtest_variant_trials")).scalar_one() == before


def test_register_command_runs_as_research(server_like, monkeypatch) -> None:
    hg.apply_policies(server_like["app"])
    hg.apply_research_registry_policies(server_like["app"])
    from src.simulation import register

    monkeypatch.setattr(hg, "_research_engine", hg.install(server_like["research"]))
    first = register.register()
    second = register.register()
    assert first["role"] == hg.RESEARCH_ROLE
    assert (first["inserted"], second["inserted"]) == (1, 0)
    assert second["family_total"] == first["family_total"] >= 1
