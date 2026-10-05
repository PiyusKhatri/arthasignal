from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

WRITERS: dict[str, tuple[str, ...]] = {
    "chain": ("ops_runs", "ops_alerts"),
    "capture": ("companies", "daily_prices", "market_index", "trading_calendar"),
    "integrity": ("price_quarantine", "corporate_actions", "corporate_action_sources"),
    "league": ("scorecard_models", "scorecard_calls", "league_runs", "league_leaderboard", "backtest_variant_trials"),
    "avoid_writer": ("scorecard_models", "scorecard_calls", "backtest_variant_trials"),
    "tips": ("public_tips", "public_tip_events", "tip_leaderboard", "scorecard_calls", "text_items", "backtest_variant_trials"),
    "corporate_actions": ("corporate_actions",),
    "quarterly_capture": ("quarterly_figure_captures", "quarterly_capture_runs", "quarterly_collector_progress",
                          "quarterly_report_announcements", "quarterly_report_figures", "dividend_declarations"),
    "news": ("text_items", "text_collector_runs", "text_items_ephemeral"),
    "grading": ("scorecard_grades",),
    "metrics": ("league_leaderboard", "tip_leaderboard"),
    "report": ("ops_alerts", "ops_runs"),
}
RESEARCH_WRITES = ("backtest_variant_trials",)
RESEARCH_REFUSED = ("backtest_holdout_evaluations", "scorecard_calls", "scorecard_grades", "scorecard_models", "daily_prices")
TODAY = "(now() AT TIME ZONE 'Asia/Kathmandu')::date"
DATE_TYPES = {"date"}
TIME_TYPES = {"timestamp without time zone", "timestamp with time zone"}
EMPTY_VALUES = {
    "date": TODAY, "timestamp without time zone": "timezone('utc', now())", "timestamp with time zone": "now()",
    "boolean": "false", "jsonb": "'{}'::jsonb", "json": "'{}'::json", "uuid": "gen_random_uuid()",
}


def columns(connection: Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in connection.execute(text(
        "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, t.typname, a.attnotnull AS not_null, "
        "a.attidentity <> '' OR a.attgenerated <> '' OR pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval(%' AS generated, "
        "d.adbin IS NOT NULL AS has_default, a.atttypmod "
        "FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = to_regclass(:t) AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"), {"t": f"public.{table}"})]


def unique_key_columns(connection: Connection, table: str) -> set[str]:
    return {r[0] for r in connection.execute(text(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "JOIN pg_type t ON t.oid = a.atttypid WHERE i.indrelid = to_regclass(:t) AND i.indisunique "
        "AND t.typcategory IN ('S', 'U')"), {"t": f"public.{table}"})}


def allowed_literals(connection: Connection, table: str) -> dict[str, str]:
    out = {}
    for name, definition in connection.execute(text(
            "SELECT a.attname, pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
            "AND a.attnum = c.conkey[1] WHERE c.conrelid = to_regclass(:t) AND c.contype = 'c' AND cardinality(c.conkey) = 1"),
            {"t": f"public.{table}"}):
        literal = re.search(r"'([^']*)'::", definition)
        if literal:
            out[name] = literal.group(1).replace("'", "''")
    return out


def foreign_keys(connection: Connection, table: str) -> dict[str, tuple[str, str]]:
    return {r[0]: (r[1], r[2]) for r in connection.execute(text(
        "SELECT a.attname, c.confrelid::regclass::text, f.attname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
        "JOIN pg_attribute f ON f.attrelid = c.confrelid AND f.attnum = c.confkey[1] "
        "WHERE c.conrelid = to_regclass(:t) AND c.contype = 'f' AND cardinality(c.conkey) = 1"), {"t": f"public.{table}"})}


def parents(connection: Connection, table: str) -> set[str]:
    return {r[0] for r in connection.execute(text(
        "SELECT confrelid::regclass::text FROM pg_constraint WHERE conrelid = to_regclass(:t) AND contype = 'f'"), {"t": f"public.{table}"})}


def dependency_order(connection: Connection, tables: list[str]) -> list[str]:
    ordered: list[str] = []
    pending = {t: parents(connection, t) & set(tables) - {t} for t in tables}
    while pending:
        ready = sorted(t for t, deps in pending.items() if not deps - set(ordered)) or sorted(pending)[:1]
        for t in ready:
            ordered.append(t)
            pending.pop(t)
    return ordered


def _base_type(column: dict[str, Any]) -> str:
    return column["type"].split("(")[0]


def _fresh(column: dict[str, Any], token: str) -> str:
    size = column["atttypmod"] - 4 if column["type"].startswith("character") and column["atttypmod"] > 4 else 64
    return f"left('{token}', {size})"


def _empty(column: dict[str, Any], token: str) -> str:
    base = _base_type(column)
    if base == "uuid":
        return "gen_random_uuid()"
    if base in EMPTY_VALUES:
        return EMPTY_VALUES[base]
    if column["type"].endswith("[]"):
        return f"'{{}}'::{column['type']}"
    if base in ("text", "character varying", "character"):
        return _fresh(column, token)
    return f"0::{column['type']}"


def insert_statement(connection: Connection, table: str, token: str, fresh_keys: bool) -> str:
    cols = [c for c in columns(connection, table) if not c["generated"]]
    has_rows = connection.execute(text(f"SELECT EXISTS (SELECT 1 FROM public.{table})")).scalar()
    keys = unique_key_columns(connection, table) if fresh_keys else set()
    literals = allowed_literals(connection, table)
    references = foreign_keys(connection, table)
    names, values = [], []
    for column in cols:
        base = _base_type(column)
        quoted = f'"{column["name"]}"'
        if base in DATE_TYPES:
            value = TODAY
        elif base in TIME_TYPES:
            value = EMPTY_VALUES[base]
        elif column["name"] in keys:
            value = "gen_random_uuid()" if base == "uuid" else _fresh(column, token)
        elif has_rows:
            value = quoted
        elif column["name"] in references:
            parent, key = references[column["name"]]
            value = f'(SELECT "{key}" FROM {parent} LIMIT 1)'
        elif column["name"] in literals:
            value = f"'{literals[column['name']]}'"
        elif column["not_null"] and not column["has_default"]:
            value = _empty(column, token)
        else:
            continue
        names.append(quoted)
        values.append(value)
    source = f" FROM public.{table} LIMIT 1" if has_rows else ""
    return f"INSERT INTO public.{table} ({', '.join(names)}) SELECT {', '.join(values)}{source}"


def probe(connection: Connection, table: str) -> dict[str, Any]:
    if connection.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar() is None:
        return {"table": table, "result": "missing"}
    token = f"write-check-{uuid.uuid4().hex[:12]}"
    last: dict[str, Any] = {}
    for fresh_keys in (False, True):
        savepoint = connection.begin_nested()
        try:
            inserted = connection.execute(text(insert_statement(connection, table, token, fresh_keys))).rowcount
            savepoint.commit()
            return {"table": table, "result": "ok", "rows": inserted}
        except DBAPIError as exc:
            savepoint.rollback()
            code = getattr(exc.orig, "pgcode", None) or ""
            message = str(exc.orig).splitlines()[0]
            if code == "42501":
                return {"table": table, "result": "REFUSED", "sqlstate": code, "error": message}
            last = {"table": table, "result": "permitted_but_row_rejected" if code.startswith("23") else "inconclusive",
                    "sqlstate": code, "error": message}
            if code != "23505":
                break
    return last


def check_app(engine: Engine) -> dict[str, Any]:
    tables = sorted({t for group in WRITERS.values() for t in group})
    with engine.connect() as connection:
        role = connection.execute(text("SELECT current_user")).scalar_one()
        try:
            results = {t: probe(connection, t) for t in dependency_order(connection, tables)}
            extra = []
            for label, statement in (
                ("update ops_alerts", "UPDATE public.ops_alerts SET sent = sent WHERE id = (SELECT max(id) FROM public.ops_alerts)"),
                ("delete expired text_items_ephemeral", "DELETE FROM public.text_items_ephemeral WHERE expires_at <= now() - interval '100 years'"),
            ):
                savepoint = connection.begin_nested()
                try:
                    connection.execute(text(statement))
                    extra.append({"statement": label, "result": "ok"})
                except DBAPIError as exc:
                    extra.append({"statement": label, "result": "REFUSED" if getattr(exc.orig, "pgcode", "") == "42501" else "inconclusive",
                                  "error": str(exc.orig).splitlines()[0]})
                savepoint.rollback()
        finally:
            connection.rollback()
    writers = {w: {t: results[t]["result"] for t in group} for w, group in WRITERS.items()}
    return {"role": role, "writers": writers, "details": [r for r in results.values() if r["result"] != "ok"], "other_statements": extra,
            "missing_created_by_writer_on_first_run": sorted(t for t, r in results.items() if r["result"] == "missing"),
            "refused": sorted({t for t, r in results.items() if r["result"] in ("REFUSED", "inconclusive")}
                              | {e["statement"] for e in extra if e["result"] != "ok"})}


def check_research(engine: Engine) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with engine.connect() as connection:
        out["role"] = connection.execute(text("SELECT current_user")).scalar_one()
        try:
            out["writes"] = {t: probe(connection, t) for t in RESEARCH_WRITES}
            token = f"write-check-{uuid.uuid4().hex[:12]}"
            savepoint = connection.begin_nested()
            try:
                connection.execute(text(
                    "INSERT INTO backtest_variant_trials (model_family, variant_fingerprint, description, created_at) "
                    "VALUES (:f, :p, 'write check, rolled back', timezone('utc', now()))"), {"f": token, "p": token})
                savepoint.commit()
            except DBAPIError:
                savepoint.rollback()
            out["registry_row_visible_after_insert"] = connection.execute(text(
                "SELECT count(*) FROM backtest_variant_trials WHERE model_family = :f"), {"f": token}).scalar_one() == 1
            out["must_stay_refused"] = {t: probe(connection, t)["result"] for t in RESEARCH_REFUSED}
            out["holdout_evaluations_visible_rows"] = connection.execute(text("SELECT count(*) FROM backtest_holdout_evaluations")).scalar_one()
            out["latest_visible_price_date"] = str(connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one())
        finally:
            connection.rollback()
    from src.database.holdout_guard import HOLDOUT_START

    out["holdout_hidden"] = out["latest_visible_price_date"] < HOLDOUT_START.isoformat()
    out["nothing_else_opened"] = (all(v in ("REFUSED", "missing") for v in out["must_stay_refused"].values())
                                  and out["holdout_evaluations_visible_rows"] == 0)
    out["ok"] = (all(r["result"] == "ok" for r in out["writes"].values()) and out["registry_row_visible_after_insert"]
                 and out["holdout_hidden"] and out["nothing_else_opened"])
    return out


def run() -> dict[str, Any]:
    from src.database.connection import engine
    from src.database.holdout_guard import research_engine

    app = check_app(engine)
    research = check_research(research_engine())
    return {"mode": "write check: one real insert per writer table dated today, every transaction rolled back",
            "app": app, "research": research, "ok": not app["refused"] and research["ok"]}


def main() -> None:
    argparse.ArgumentParser(description="One real insert per live writer table, rolled back").parse_args()
    result = run()
    print(json.dumps(result, indent=1, default=str))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
