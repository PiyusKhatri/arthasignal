from __future__ import annotations

import argparse

from sqlalchemy import text
from sqlalchemy.engine import Engine

DDL = """
CREATE TABLE IF NOT EXISTS {schema}.scorecard_calls (
    id              BIGSERIAL PRIMARY KEY,
    call_uid        UUID NOT NULL UNIQUE,
    mode            VARCHAR(10) NOT NULL CHECK (mode IN ('replay', 'live')),
    strategy        VARCHAR(80) NOT NULL,
    model_version   VARCHAR(80) NOT NULL,
    feature_hash    CHAR(64) NOT NULL,
    batch_id        VARCHAR(80) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    signal_date     DATE NOT NULL,
    direction       VARCHAR(10) NOT NULL DEFAULT 'long' CHECK (direction = 'long'),
    probability     NUMERIC(6, 5) NOT NULL CHECK (probability >= 0 AND probability <= 1),
    score           DOUBLE PRECISION,
    situations      TEXT[] NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (strategy, model_version, symbol, signal_date)
);

ALTER TABLE {schema}.scorecard_calls DROP CONSTRAINT IF EXISTS scorecard_calls_check;

ALTER TABLE {schema}.scorecard_calls ALTER COLUMN probability DROP NOT NULL;

CREATE INDEX IF NOT EXISTS ix_scorecard_calls_strategy_version
    ON {schema}.scorecard_calls (strategy, model_version, signal_date);

CREATE TABLE IF NOT EXISTS {schema}.scorecard_grades (
    id              BIGSERIAL PRIMARY KEY,
    call_id         BIGINT NOT NULL REFERENCES {schema}.scorecard_calls (id),
    grade_version   VARCHAR(20) NOT NULL,
    horizon         SMALLINT NOT NULL,
    horizon_class   VARCHAR(5) NOT NULL,
    status          VARCHAR(12) NOT NULL,
    entry_rule      VARCHAR(10),
    entry_date      DATE,
    exit_date       DATE,
    gross_return    DOUBLE PRECISION,
    universe_median DOUBLE PRECISION,
    universe_mean   DOUBLE PRECISION,
    sector_median   DOUBLE PRECISION,
    nepse_return    DOUBLE PRECISION,
    baseline_share  DOUBLE PRECISION,
    correct         BOOLEAN NOT NULL,
    failure_cause   VARCHAR(12),
    graded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (call_id, grade_version, horizon)
);

CREATE INDEX IF NOT EXISTS ix_scorecard_grades_call ON {schema}.scorecard_grades (call_id);

CREATE TABLE IF NOT EXISTS {schema}.scorecard_models (
    id              BIGSERIAL PRIMARY KEY,
    model_name      VARCHAR(80) NOT NULL,
    model_version   VARCHAR(80) NOT NULL,
    description     TEXT NOT NULL,
    parameters      JSONB NOT NULL,
    parameters_hash CHAR(64) NOT NULL,
    code_commit     VARCHAR(40) NOT NULL,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_name, model_version)
);

CREATE OR REPLACE FUNCTION {schema}.scorecard_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'scorecard ledger is append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS {schema}.nepse_calendar_rules (
    id              BIGSERIAL PRIMARY KEY,
    rule_key        VARCHAR(40) NOT NULL UNIQUE,
    kind            VARCHAR(10) NOT NULL CHECK (kind IN ('weekdays', 'holiday')),
    effective_from  DATE,
    weekdays        SMALLINT[],
    holiday         DATE,
    source          TEXT NOT NULL,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'weekdays' AND effective_from IS NOT NULL AND weekdays IS NOT NULL) OR (kind = 'holiday' AND holiday IS NOT NULL))
);

DROP TRIGGER IF EXISTS nepse_calendar_rules_immutable ON {schema}.nepse_calendar_rules;
CREATE TRIGGER nepse_calendar_rules_immutable BEFORE UPDATE OR DELETE ON {schema}.nepse_calendar_rules
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();

CREATE OR REPLACE FUNCTION {schema}.scorecard_next_open(signal DATE) RETURNS TIMESTAMPTZ AS $$
DECLARE
    day DATE := signal + 1;
    allowed SMALLINT[];
BEGIN
    FOR step IN 1..45 LOOP
        IF EXISTS (SELECT 1 FROM public.daily_prices WHERE date = day) THEN
            RETURN (day + TIME '11:00') AT TIME ZONE 'Asia/Kathmandu';
        END IF;
        SELECT r.weekdays INTO allowed FROM {schema}.nepse_calendar_rules r
            WHERE r.kind = 'weekdays' AND r.effective_from <= day ORDER BY r.effective_from DESC LIMIT 1;
        IF allowed IS NOT NULL AND (extract(isodow FROM day)::SMALLINT - 1) = ANY(allowed)
           AND NOT EXISTS (SELECT 1 FROM {schema}.nepse_calendar_rules h WHERE h.kind = 'holiday' AND h.holiday = day) THEN
            RETURN (day + TIME '11:00') AT TIME ZONE 'Asia/Kathmandu';
        END IF;
        day := day + 1;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION {schema}.scorecard_live_deadline() RETURNS trigger AS $$
DECLARE
    deadline TIMESTAMPTZ;
BEGIN
    IF NEW.mode = 'live' THEN
        deadline := {schema}.scorecard_next_open(NEW.signal_date);
        IF deadline IS NULL OR NEW.created_at >= deadline THEN
            RAISE EXCEPTION 'accuracy-v2.1: live call for % created at % is not before the next session open %', NEW.signal_date, NEW.created_at, deadline;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scorecard_calls_live_deadline ON {schema}.scorecard_calls;
CREATE TRIGGER scorecard_calls_live_deadline BEFORE INSERT ON {schema}.scorecard_calls
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_live_deadline();
DROP TRIGGER IF EXISTS scorecard_calls_immutable ON {schema}.scorecard_calls;
CREATE TRIGGER scorecard_calls_immutable BEFORE UPDATE OR DELETE ON {schema}.scorecard_calls
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
DROP TRIGGER IF EXISTS scorecard_calls_no_truncate ON {schema}.scorecard_calls;
CREATE TRIGGER scorecard_calls_no_truncate BEFORE TRUNCATE ON {schema}.scorecard_calls
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.scorecard_reject_change();

DROP TRIGGER IF EXISTS scorecard_models_immutable ON {schema}.scorecard_models;
CREATE TRIGGER scorecard_models_immutable BEFORE UPDATE OR DELETE ON {schema}.scorecard_models
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
DROP TRIGGER IF EXISTS scorecard_models_no_truncate ON {schema}.scorecard_models;
CREATE TRIGGER scorecard_models_no_truncate BEFORE TRUNCATE ON {schema}.scorecard_models
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.scorecard_reject_change();

DROP TRIGGER IF EXISTS scorecard_grades_immutable ON {schema}.scorecard_grades;
CREATE TRIGGER scorecard_grades_immutable BEFORE UPDATE OR DELETE ON {schema}.scorecard_grades
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
DROP TRIGGER IF EXISTS scorecard_grades_no_truncate ON {schema}.scorecard_grades;
CREATE TRIGGER scorecard_grades_no_truncate BEFORE TRUNCATE ON {schema}.scorecard_grades
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.scorecard_reject_change();
"""


def apply_schema(engine: Engine, schema: str = "public", calendar: bool = True) -> None:
    statement = DDL.format(schema=schema)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(statement)
        raw.commit()
    finally:
        raw.close()
    if calendar:
        sync_calendar(engine, schema)


def sync_calendar(engine: Engine, schema: str = "public", path: str | None = None) -> int:
    import json
    from pathlib import Path

    from src.scorecard.calendar import CONFIG_PATH, DAY_NAMES

    config = json.loads(Path(path or CONFIG_PATH).read_text())
    rows = [{"k": f"weekdays:{r['effective_from']}", "kind": "weekdays", "e": r["effective_from"],
             "w": [DAY_NAMES.index(d) for d in r["weekdays"]], "h": None, "s": r.get("source", "")} for r in config["weekday_rules"]]
    for holiday in config.get("holidays", []):
        day, source = (holiday, "") if isinstance(holiday, str) else (holiday["date"], holiday.get("source", ""))
        rows.append({"k": f"holiday:{day}", "kind": "holiday", "e": None, "w": None, "h": day, "s": source})
    inserted = 0
    with engine.begin() as connection:
        for row in rows:
            result = connection.execute(
                text(f"INSERT INTO {schema}.nepse_calendar_rules (rule_key, kind, effective_from, weekdays, holiday, source) "
                     "VALUES (:k, :kind, :e, :w, :h, :s) ON CONFLICT (rule_key) DO NOTHING RETURNING id"),
                row,
            ).first()
            inserted += int(result is not None)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default="public")
    args = parser.parse_args()
    from src.database.connection import engine

    apply_schema(engine, args.schema)
    with engine.connect() as connection:
        triggers = connection.execute(
            text(
                "SELECT event_object_table, trigger_name, event_manipulation FROM information_schema.triggers "
                "WHERE trigger_schema = :s AND event_object_table LIKE 'scorecard_%' ORDER BY 1, 2, 3"
            ),
            {"s": args.schema},
        ).all()
    for row in triggers:
        print(*row)


if __name__ == "__main__":
    main()
