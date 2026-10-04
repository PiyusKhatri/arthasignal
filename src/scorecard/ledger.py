from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.scorecard import spec

CALL_COLUMNS = (
    "call_uid", "mode", "strategy", "model_version", "feature_hash", "batch_id", "symbol",
    "signal_date", "probability", "score", "situations",
)
GRADE_COLUMNS = (
    "call_id", "grade_version", "horizon", "horizon_class", "status", "entry_rule", "entry_date", "exit_date",
    "gross_return", "universe_median", "universe_mean", "sector_median", "nepse_return", "baseline_share",
    "correct", "failure_cause",
)


def feature_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _pg_array(values: Sequence[str]) -> str:
    return "{" + ",".join('"' + v.replace('"', '\\"') + '"' for v in values) + "}"


def _copy(engine: Engine, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]], schema: str) -> int:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    count = 0
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
        count += 1
    buffer.seek(0)
    column_list = ", ".join(columns)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute("SET statement_timeout = 0")
            cursor.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (f"{schema}.{table}",))
            secured = bool(cursor.fetchone()[0])
            if secured:
                cursor.execute(f"CREATE TEMP TABLE _ledger_stage (LIKE {schema}.{table} INCLUDING DEFAULTS) ON COMMIT DROP")
                cursor.copy_expert(f"COPY _ledger_stage ({column_list}) FROM STDIN WITH (FORMAT csv, NULL '')", buffer)
                cursor.execute(f"INSERT INTO {schema}.{table} ({column_list}) SELECT {column_list} FROM _ledger_stage")
            else:
                cursor.copy_expert(f"COPY {schema}.{table} ({column_list}) FROM STDIN WITH (FORMAT csv, NULL '')", buffer)
        raw.commit()
    finally:
        raw.close()
    return count


def record_calls(engine: Engine, calls: pd.DataFrame, schema: str = "public") -> pd.DataFrame:
    frame = calls.copy()
    if "call_uid" not in frame:
        frame["call_uid"] = [str(uuid.uuid4()) for _ in range(len(frame))]
    if "probability" not in frame:
        frame["probability"] = spec.DEFAULT_PROBABILITY
    if "score" not in frame:
        frame["score"] = None
    rows = (
        (
            r.call_uid, r.mode, r.strategy, r.model_version, r.feature_hash, r.batch_id, r.symbol,
            r.signal_date.isoformat(),
            None if r.probability is None or pd.isna(r.probability) else f"{float(r.probability):.5f}",
            None if r.score is None or pd.isna(r.score) else float(r.score),
            _pg_array(list(r.situations)),
        )
        for r in frame.itertuples(index=False)
    )
    _copy(engine, "scorecard_calls", CALL_COLUMNS, rows, schema)
    return frame


def record_grades(engine: Engine, grades: pd.DataFrame, schema: str = "public") -> int:
    def fmt(value: Any) -> Any:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, bool):
            return "t" if value else "f"
        return value

    rows = ([fmt(getattr(r, column)) for column in GRADE_COLUMNS] for r in grades.itertuples(index=False))
    return _copy(engine, "scorecard_grades", GRADE_COLUMNS, rows, schema)


def load_calls(engine: Engine, strategy: str | None = None, schema: str = "public") -> pd.DataFrame:
    query = f"SELECT * FROM {schema}.scorecard_calls"
    params: dict[str, Any] = {}
    if strategy is not None:
        query += " WHERE strategy = :strategy"
        params["strategy"] = strategy
    with engine.connect() as connection:
        return pd.read_sql(text(query), connection, params=params)


def load_graded(
    engine: Engine,
    strategy: str,
    model_version: str,
    schema: str = "public",
    horizon: int | None = None,
    grade_version: str = spec.GRADE_VERSION,
) -> pd.DataFrame:
    query = f"""
        SELECT c.id AS call_id, c.mode, c.strategy, c.model_version, c.symbol, c.signal_date, c.probability,
               c.score, c.situations, g.horizon, g.horizon_class, g.status, g.entry_rule, g.entry_date, g.exit_date,
               g.gross_return, g.universe_median, g.universe_mean, g.sector_median, g.nepse_return,
               g.baseline_share, g.correct, g.failure_cause
        FROM {schema}.scorecard_calls c
        JOIN {schema}.scorecard_grades g ON g.call_id = c.id AND g.grade_version = :grade_version
        WHERE c.strategy = :strategy AND c.model_version = :model_version
    """
    params: dict[str, Any] = {"strategy": strategy, "model_version": model_version, "grade_version": grade_version}
    if horizon is not None:
        query += " AND g.horizon = :horizon"
        params["horizon"] = horizon
    with engine.connect() as connection:
        frame = pd.read_sql(text(query), connection, params=params)
    frame["probability"] = pd.to_numeric(frame["probability"], errors="coerce")
    return frame


def call_ids(engine: Engine, uids: Sequence[str], schema: str = "public") -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"SELECT call_uid::text, id FROM {schema}.scorecard_calls WHERE call_uid = ANY(CAST(:u AS uuid[]))"),
            {"u": list(uids)},
        ).all()
    return {uid: int(i) for uid, i in rows}


def graded_call_ids(
    engine: Engine, strategy: str, model_version: str, schema: str = "public", grade_version: str = spec.GRADE_VERSION
) -> set[tuple[int, int]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT g.call_id, g.horizon FROM {schema}.scorecard_grades g JOIN {schema}.scorecard_calls c "
                "ON c.id = g.call_id WHERE c.strategy = :s AND c.model_version = :m AND g.grade_version = :v"
            ),
            {"s": strategy, "m": model_version, "v": grade_version},
        ).all()
    return {(int(a), int(b)) for a, b in rows}
