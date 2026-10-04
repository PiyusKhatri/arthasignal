from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

DDL = """
CREATE TABLE IF NOT EXISTS {schema}.ops_runs (
    id           BIGSERIAL PRIMARY KEY,
    job          VARCHAR(40) NOT NULL,
    run_key      VARCHAR(80) NOT NULL,
    step         VARCHAR(40) NOT NULL,
    status       VARCHAR(12) NOT NULL CHECK (status IN ('ok', 'failed', 'skipped', 'no_session', 'timeout')),
    exit_code    INTEGER,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    host         VARCHAR(80) NOT NULL,
    detail       JSONB NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_ops_runs_job_time ON {schema}.ops_runs (job, finished_at);
ALTER TABLE {schema}.ops_runs DROP CONSTRAINT IF EXISTS ops_runs_status_check;
ALTER TABLE {schema}.ops_runs ADD CONSTRAINT ops_runs_status_check
    CHECK (status IN ('ok', 'failed', 'skipped', 'no_session', 'timeout', 'flagged'));

CREATE TABLE IF NOT EXISTS {schema}.ops_alerts (
    id           BIGSERIAL PRIMARY KEY,
    fingerprint  VARCHAR(200) NOT NULL UNIQUE,
    message      TEXT NOT NULL,
    sent         BOOLEAN NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION {schema}.ops_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ops log is append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ops_runs_immutable ON {schema}.ops_runs;
CREATE TRIGGER ops_runs_immutable BEFORE UPDATE OR DELETE ON {schema}.ops_runs
    FOR EACH ROW EXECUTE FUNCTION {schema}.ops_reject_change();
"""


def apply_schema(engine: Engine, schema: str = "public") -> None:
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(DDL.format(schema=schema))
        raw.commit()
    finally:
        raw.close()


def record(engine: Engine, job: str, run_key: str, step: str, status: str, exit_code: int | None, started_at: datetime,
           detail: dict[str, Any] | None = None, schema: str = "public") -> None:
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO {schema}.ops_runs (job, run_key, step, status, exit_code, started_at, host, detail) "
                 "VALUES (:j, :k, :s, :st, :e, :a, :h, CAST(:d AS jsonb))"),
            {"j": job, "k": run_key, "s": step, "st": status, "e": exit_code, "a": started_at, "h": socket.gethostname()[:80],
             "d": json.dumps(detail or {}, default=str)},
        )


def alert_once(engine: Engine, fingerprint: str, message: str, severity: str = "failure", schema: str = "public") -> bool:
    from src.notifications.discord_alert import send_discord_alert

    with engine.begin() as connection:
        row = connection.execute(
            text(f"INSERT INTO {schema}.ops_alerts (fingerprint, message, sent) VALUES (:f, :m, false) "
                 "ON CONFLICT (fingerprint) DO NOTHING RETURNING id"),
            {"f": fingerprint[:200], "m": message},
        ).first()
    if row is None:
        return False
    sent = send_discord_alert(message[:3900], severity)
    with engine.begin() as connection:
        connection.execute(text(f"UPDATE {schema}.ops_alerts SET sent = :s WHERE id = :i"), {"s": sent, "i": row.id})
    return sent


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
