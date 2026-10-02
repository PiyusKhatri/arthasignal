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
    UNIQUE (strategy, model_version, symbol, signal_date),
    CHECK (mode = 'replay' OR created_at < (((signal_date + 1)::timestamp + interval '11 hours') AT TIME ZONE 'Asia/Kathmandu'))
);

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

CREATE OR REPLACE FUNCTION {schema}.scorecard_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'scorecard ledger is append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS scorecard_calls_immutable ON {schema}.scorecard_calls;
CREATE TRIGGER scorecard_calls_immutable BEFORE UPDATE OR DELETE ON {schema}.scorecard_calls
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
DROP TRIGGER IF EXISTS scorecard_calls_no_truncate ON {schema}.scorecard_calls;
CREATE TRIGGER scorecard_calls_no_truncate BEFORE TRUNCATE ON {schema}.scorecard_calls
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.scorecard_reject_change();

DROP TRIGGER IF EXISTS scorecard_grades_immutable ON {schema}.scorecard_grades;
CREATE TRIGGER scorecard_grades_immutable BEFORE UPDATE OR DELETE ON {schema}.scorecard_grades
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
DROP TRIGGER IF EXISTS scorecard_grades_no_truncate ON {schema}.scorecard_grades;
CREATE TRIGGER scorecard_grades_no_truncate BEFORE TRUNCATE ON {schema}.scorecard_grades
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.scorecard_reject_change();
"""


def apply_schema(engine: Engine, schema: str = "public") -> None:
    statement = DDL.format(schema=schema)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(statement)
        raw.commit()
    finally:
        raw.close()


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
