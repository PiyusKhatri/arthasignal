from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

PERMANENT = "permanent"
THIRTY_DAYS = "30_days"
PRECISIONS = ("second", "minute", "day", "none")

DDL = """
CREATE OR REPLACE FUNCTION {schema}.text_items_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'text store is append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS {schema}.text_items (
    id                   BIGSERIAL PRIMARY KEY,
    source               VARCHAR(40) NOT NULL,
    channel              VARCHAR(200) NOT NULL DEFAULT '',
    source_item_id       VARCHAR(300) NOT NULL,
    url                  TEXT,
    title                TEXT,
    body                 TEXT,
    author_hash          CHAR(64),
    published_at         TIMESTAMPTZ,
    published_precision  VARCHAR(10) NOT NULL CHECK (published_precision IN ('second', 'minute', 'day', 'none')),
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    language             VARCHAR(8),
    symbols              TEXT[] NOT NULL DEFAULT '{{}}',
    content_sha256       CHAR(64) NOT NULL,
    retention            VARCHAR(10) NOT NULL DEFAULT 'permanent' CHECK (retention IN ('permanent', '30_days')),
    raw                  JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    collector_version    VARCHAR(20) NOT NULL,
    UNIQUE (source, source_item_id, content_sha256),
    CHECK (retention = 'permanent' OR (body IS NULL AND title IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_text_items_source_seen ON {schema}.text_items (source, first_seen_at);
CREATE INDEX IF NOT EXISTS ix_text_items_symbols ON {schema}.text_items USING gin (symbols);

CREATE TABLE IF NOT EXISTS {schema}.text_items_ephemeral (
    item_id      BIGINT PRIMARY KEY REFERENCES {schema}.text_items (id),
    title        TEXT,
    body         TEXT,
    stored_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.text_collector_runs (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(40) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          VARCHAR(20) NOT NULL,
    items_seen      INTEGER NOT NULL,
    items_inserted  INTEGER NOT NULL,
    errors          INTEGER NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    collector_version VARCHAR(20) NOT NULL
);

DROP TRIGGER IF EXISTS text_items_immutable ON {schema}.text_items;
CREATE TRIGGER text_items_immutable BEFORE UPDATE OR DELETE ON {schema}.text_items
    FOR EACH ROW EXECUTE FUNCTION {schema}.text_items_reject_change();
DROP TRIGGER IF EXISTS text_items_no_truncate ON {schema}.text_items;
CREATE TRIGGER text_items_no_truncate BEFORE TRUNCATE ON {schema}.text_items
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.text_items_reject_change();
DROP TRIGGER IF EXISTS text_collector_runs_immutable ON {schema}.text_collector_runs;
CREATE TRIGGER text_collector_runs_immutable BEFORE UPDATE OR DELETE ON {schema}.text_collector_runs
    FOR EACH ROW EXECUTE FUNCTION {schema}.text_items_reject_change();
"""


def apply_schema(engine: Engine, schema: str = "public") -> None:
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(DDL.format(schema=schema))
        raw.commit()
    finally:
        raw.close()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def author_hash(source: str, author: str | None) -> str | None:
    return None if not author else sha256(f"{source}:{author}")


@dataclass
class Item:
    source: str
    source_item_id: str
    title: str | None
    body: str | None
    published_at: datetime | None
    published_precision: str
    url: str | None = None
    channel: str = ""
    author: str | None = None
    language: str | None = None
    symbols: Sequence[str] = ()
    retention: str = PERMANENT
    raw: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        return sha256(json.dumps({"title": self.title or "", "body": self.body or ""}, ensure_ascii=False, sort_keys=True))


def insert_items(engine: Engine, items: Iterable[Item], collector_version: str, schema: str = "public",
                 now: datetime | None = None) -> int:
    now = now or datetime.now(tz=timezone.utc)
    inserted = 0
    with engine.begin() as connection:
        for item in items:
            if item.published_precision not in PRECISIONS:
                raise ValueError(f"bad precision {item.published_precision}")
            ephemeral = item.retention == THIRTY_DAYS
            row = connection.execute(
                text(
                    f"INSERT INTO {schema}.text_items (source, channel, source_item_id, url, title, body, author_hash, published_at, "
                    "published_precision, first_seen_at, language, symbols, content_sha256, retention, raw, collector_version) VALUES "
                    "(:source, :channel, :sid, :url, :title, :body, :author, :published, :precision, :seen, :language, :symbols, :hash, "
                    ":retention, CAST(:raw AS jsonb), :version) ON CONFLICT (source, source_item_id, content_sha256) DO NOTHING RETURNING id"
                ),
                {"source": item.source, "channel": item.channel, "sid": item.source_item_id, "url": item.url,
                 "title": None if ephemeral else item.title, "body": None if ephemeral else item.body,
                 "author": author_hash(item.source, item.author), "published": item.published_at, "precision": item.published_precision,
                 "seen": now, "language": item.language, "symbols": sorted(set(item.symbols)), "hash": item.content_hash(),
                 "retention": item.retention, "raw": json.dumps(item.raw, ensure_ascii=False, default=str), "version": collector_version},
            ).first()
            if row is None:
                continue
            inserted += 1
            if ephemeral:
                connection.execute(
                    text(f"INSERT INTO {schema}.text_items_ephemeral (item_id, title, body, stored_at, expires_at) VALUES (:i, :t, :b, :s, :e)"),
                    {"i": row.id, "t": item.title, "b": item.body, "s": now, "e": now + timedelta(days=30)},
                )
    return inserted


def known_ids(engine: Engine, source: str, schema: str = "public") -> set[str]:
    with engine.connect() as connection:
        return {r[0] for r in connection.execute(text(f"SELECT DISTINCT source_item_id FROM {schema}.text_items WHERE source = :s"), {"s": source})}


def record_run(engine: Engine, source: str, started_at: datetime, status: str, seen: int, inserted: int, errors: int,
               detail: dict[str, Any], collector_version: str, schema: str = "public") -> None:
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO {schema}.text_collector_runs (source, started_at, status, items_seen, items_inserted, errors, detail, "
                 "collector_version) VALUES (:s, :a, :st, :n, :i, :e, CAST(:d AS jsonb), :v)"),
            {"s": source, "a": started_at, "st": status, "n": seen, "i": inserted, "e": errors,
             "d": json.dumps(detail, ensure_ascii=False, default=str), "v": collector_version},
        )


def purge_expired(engine: Engine, schema: str = "public", now: datetime | None = None) -> int:
    now = now or datetime.now(tz=timezone.utc)
    with engine.begin() as connection:
        result = connection.execute(text(f"DELETE FROM {schema}.text_items_ephemeral WHERE expires_at <= :n"), {"n": now})
    return int(result.rowcount or 0)
