from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any, Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)

HOLDOUT_START = date(2025, 9, 30)
LAST_DEVELOPMENT_DAY = date(2025, 9, 29)
GUC = "arthasignal.holdout_guard"
RESEARCH_ROLE = "arthasignal_research"
ALLOWED_REASONS = ("final_evaluation", "live_ledger")
DATE_LITERAL = re.compile(r"'(\d{4})-(\d{2})-(\d{2})")
STRICT_UPPER = re.compile(r"<\s*(?:DATE\s*)?$", re.I)

PROTECTED_COLUMNS: dict[str, str] = {
    "daily_prices": "date",
    "market_index": "date",
    "corporate_actions": "action_date",
    "corporate_action_sources": "action_date",
    "technical_signals": "date",
    "trading_calendar": "date",
    "symbol_history": "effective_date",
    "fundamentals": "reported_date",
    "promoter_holding": "reported_date",
    "intraday_floorsheet": "snapshot_time",
    "intraday_snapshots": "snapshot_time",
    "scorecard_calls": "signal_date",
    "quarterly_report_announcements": "published_date",
    "quarterly_report_figures": "published_date",
    "dividend_declarations": "announcement_date",
    "quarterly_figure_captures": "captured_at",
    "league_leaderboard": "as_of",
    "league_runs": "as_of",
    "text_items": "first_seen_at",
    "text_collector_runs": "started_at",
    "public_tips": "knowledge_at",
    "public_tip_events": "recorded_at",
    "tip_leaderboard": "as_of",
}

_allowed: ContextVar[str | None] = ContextVar("holdout_allowed", default=None)
_research_engine: Engine | None = None


class HoldoutQueryViolation(RuntimeError):
    pass


def allowed_reason() -> str | None:
    return _allowed.get()


@contextmanager
def allow(reason: str) -> Iterator[None]:
    if reason not in ALLOWED_REASONS:
        raise HoldoutQueryViolation(f"holdout access reason {reason!r} is not one of {ALLOWED_REASONS}")
    token = _allowed.set(reason)
    logger.warning("holdout access opened: %s", reason)
    try:
        yield
    finally:
        _allowed.reset(token)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date()
        except Exception:
            return None
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _values(parameters: Any) -> Iterator[Any]:
    if parameters is None:
        return
    if isinstance(parameters, dict):
        for value in parameters.values():
            yield from _values(value)
    elif isinstance(parameters, (list, tuple, set)):
        for value in parameters:
            yield from _values(value)
    else:
        yield parameters


def offending_dates(statement: str, parameters: Any) -> list[date]:
    found = []
    for value in _values(parameters):
        day = _as_date(value)
        if day is not None and day >= HOLDOUT_START:
            found.append(day)
    sql = statement or ""
    for match in DATE_LITERAL.finditer(sql):
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if day > HOLDOUT_START or (day == HOLDOUT_START and not STRICT_UPPER.search(sql[: match.start()])):
            found.append(day)
    return found


def check_statement(statement: str, parameters: Any) -> None:
    if _allowed.get() is not None:
        return
    found = offending_dates(statement, parameters)
    if found:
        raise HoldoutQueryViolation(
            f"query touches {min(found)} (holdout starts {HOLDOUT_START}); cap queries at {LAST_DEVELOPMENT_DAY} "
            "or run inside final_evaluation()"
        )


def install(engine: Engine) -> Engine:
    @event.listens_for(engine, "before_cursor_execute")
    def _guard(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        check_statement(statement, parameters)

    return engine


def research_url(base_url: str | None = None, override: str | None = None) -> URL:
    from src.config import assert_allowed_database_url

    override = override if override is not None else os.environ.get("RESEARCH_DATABASE_URL") or None
    if override:
        url = make_url(assert_allowed_database_url("RESEARCH_DATABASE_URL", override))
        if url.username != RESEARCH_ROLE:
            raise HoldoutQueryViolation(f"RESEARCH_DATABASE_URL must log in as {RESEARCH_ROLE}, not {url.username!r}")
        return url
    if base_url is None:
        from src.config import settings

        base_url = settings.database_url
    base = make_url(base_url)
    return URL.create(
        drivername=base.drivername,
        username=RESEARCH_ROLE,
        password=None,
        host=base.host,
        port=base.port,
        database=base.database,
        query=base.query,
    )


def research_engine() -> Engine:
    global _research_engine
    if _research_engine is None:
        url = research_url()
        _research_engine = install(
            create_engine(
                url,
                poolclass=QueuePool,
                pool_size=3,
                max_overflow=2,
                pool_recycle=300,
                future=True,
                connect_args={"connect_timeout": 10, "options": f"-c {GUC}=on"
                              + (" -c default_transaction_read_only=on" if os.environ.get("ARTHASIGNAL_REHEARSAL") == "1" else "")},
            )
        )
    return _research_engine


def guarded_engine() -> Engine:
    if _allowed.get() is not None:
        from src.database.connection import engine

        return engine
    return research_engine()


def __getattr__(name: str) -> Any:
    if name == "engine":
        return guarded_engine()
    raise AttributeError(name)


def apply_policies(engine: Engine) -> list[str]:
    applied = []
    with engine.begin() as connection:
        for table in PROTECTED_COLUMNS:
            exists = connection.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar()
            if exists is None:
                continue
            column = PROTECTED_COLUMNS[table]
            for statement in (
                f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS holdout_guard ON {table}",
                f"CREATE POLICY holdout_guard ON {table} FOR ALL USING ("
                f"(current_user <> '{RESEARCH_ROLE}' AND coalesce(current_setting('{GUC}', true), 'off') <> 'on') "
                f"OR {column} < DATE '{HOLDOUT_START.isoformat()}')",
            ):
                connection.execute(text(statement))
            applied.append(table)
    return applied


INCIDENT_DDL = (
    """
    CREATE TABLE IF NOT EXISTS holdout_incidents (
        id BIGSERIAL PRIMARY KEY,
        occurred_on DATE NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        query_text TEXT NOT NULL,
        rows_returned TEXT NOT NULL,
        used BOOLEAN NOT NULL,
        description TEXT NOT NULL,
        UNIQUE (occurred_on, query_text)
    )
    """,
    """
    CREATE OR REPLACE FUNCTION holdout_incidents_reject_change() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'holdout incident log is append-only: % rejected', TG_OP;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS holdout_incidents_immutable ON holdout_incidents",
    "CREATE TRIGGER holdout_incidents_immutable BEFORE UPDATE OR DELETE ON holdout_incidents "
    "FOR EACH ROW EXECUTE FUNCTION holdout_incidents_reject_change()",
)


def record_incident(engine: Engine, occurred_on: date, query_text: str, rows_returned: str, used: bool, description: str) -> bool:
    with engine.begin() as connection:
        for statement in INCIDENT_DDL:
            connection.execute(text(statement))
        row = connection.execute(
            text(
                "INSERT INTO holdout_incidents (occurred_on, query_text, rows_returned, used, description) "
                "VALUES (:o, :q, :r, :u, :d) ON CONFLICT (occurred_on, query_text) DO NOTHING RETURNING id"
            ),
            {"o": occurred_on, "q": query_text, "r": rows_returned, "u": used, "d": description},
        ).first()
    return row is not None


def main() -> None:
    from src.database.connection import engine

    print("policies applied:", apply_policies(engine))


if __name__ == "__main__":
    main()
