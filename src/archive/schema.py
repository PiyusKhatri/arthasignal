from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

RAW_ROOT = Path("~/Desktop/arthasignal-ai/raw/archive").expanduser()

DATE_COLUMNS = {
    "corporate_announcements": "published_date",
    "archive_documents": "published_date",
    "news_articles": "published_at",
    "news_symbol_mentions": "published_at",
    "report_field_values": "published_date",
    "policy_events": "announced_date",
    "sentiment_observations": "published_date",
    "company_event_records": "reference_date",
    "announcement_events": "published_date",
}

DDL = r"""
CREATE OR REPLACE FUNCTION archive_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'archive tables are append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS archive_progress (
    collector   VARCHAR(60) NOT NULL,
    item_key    VARCHAR(300) NOT NULL,
    status      VARCHAR(20) NOT NULL,
    rows_added  INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collector, item_key)
);

CREATE TABLE IF NOT EXISTS corporate_announcements (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(40) NOT NULL,
    source_id       TEXT NOT NULL,
    symbol          VARCHAR(20),
    title           TEXT NOT NULL,
    url             TEXT,
    category        VARCHAR(40) NOT NULL,
    published_date  DATE NOT NULL,
    slug_date       DATE,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_corporate_announcements_symbol ON corporate_announcements (symbol, published_date);
CREATE INDEX IF NOT EXISTS ix_corporate_announcements_category ON corporate_announcements (category, published_date);

CREATE TABLE IF NOT EXISTS archive_documents (
    id                  BIGSERIAL PRIMARY KEY,
    source              VARCHAR(40) NOT NULL,
    announcement_source_id TEXT NOT NULL,
    symbol              VARCHAR(20),
    url                 TEXT NOT NULL,
    sha256              CHAR(64) NOT NULL,
    content_type        VARCHAR(80),
    bytes               INTEGER NOT NULL,
    path                TEXT NOT NULL,
    uploaded_at         TIMESTAMPTZ,
    published_date      DATE NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (url)
);
CREATE INDEX IF NOT EXISTS ix_archive_documents_announcement ON archive_documents (source, announcement_source_id);

CREATE TABLE IF NOT EXISTS news_articles (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(40) NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    category        TEXT,
    published_at    TIMESTAMPTZ NOT NULL,
    published_precision VARCHAR(10) NOT NULL,
    body            TEXT,
    body_sha256     CHAR(64),
    raw_sha256      CHAR(64),
    raw_path        TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_news_articles_published ON news_articles (source, published_at);

CREATE TABLE IF NOT EXISTS news_symbol_mentions (
    article_id      BIGINT NOT NULL REFERENCES news_articles (id),
    symbol          VARCHAR(20) NOT NULL,
    method          VARCHAR(40) NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (article_id, symbol, method)
);
CREATE INDEX IF NOT EXISTS ix_news_symbol_mentions_symbol ON news_symbol_mentions (symbol, published_at);

CREATE TABLE IF NOT EXISTS report_field_values (
    id              BIGSERIAL PRIMARY KEY,
    document_sha256 CHAR(64) NOT NULL,
    symbol          VARCHAR(20),
    fiscal_year     VARCHAR(12),
    quarter         SMALLINT,
    field           VARCHAR(40) NOT NULL,
    value           NUMERIC,
    raw_text        TEXT,
    method          VARCHAR(40) NOT NULL,
    accepted        BOOLEAN NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    published_date  DATE NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_sha256, field, method)
);
CREATE INDEX IF NOT EXISTS ix_report_field_values_symbol ON report_field_values (symbol, field, published_date);

CREATE TABLE IF NOT EXISTS policy_events (
    id              BIGSERIAL PRIMARY KEY,
    authority       VARCHAR(20) NOT NULL,
    event_type      VARCHAR(40) NOT NULL,
    measure         VARCHAR(80) NOT NULL,
    value_text      TEXT NOT NULL,
    value_num       NUMERIC,
    effective_date  DATE,
    announced_date  DATE NOT NULL,
    source_url      TEXT NOT NULL,
    source_title    TEXT NOT NULL,
    evidence        TEXT NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (authority, measure, announced_date, value_text)
);

CREATE TABLE IF NOT EXISTS company_event_records (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(40) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    record_type     VARCHAR(20) NOT NULL,
    record_key      TEXT NOT NULL,
    fiscal_year     VARCHAR(12),
    event_date      DATE,
    bookclose_date  DATE,
    announced_date  DATE,
    cash_pct        NUMERIC,
    bonus_pct       NUMERIC,
    details         JSONB NOT NULL,
    reference_date  DATE NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, symbol, record_type, record_key)
);

CREATE TABLE IF NOT EXISTS announcement_events (
    announcement_id BIGINT NOT NULL REFERENCES corporate_announcements (id),
    version         VARCHAR(20) NOT NULL,
    symbol          VARCHAR(20),
    event_type      VARCHAR(40) NOT NULL,
    fiscal_year     VARCHAR(12),
    cash_pct        NUMERIC,
    bonus_pct       NUMERIC,
    right_ratio     VARCHAR(20),
    published_date  DATE NOT NULL,
    PRIMARY KEY (announcement_id, version)
);
CREATE INDEX IF NOT EXISTS ix_announcement_events_type ON announcement_events (event_type, published_date);

CREATE OR REPLACE VIEW dividend_proposals_pit WITH (security_invoker = true) AS
SELECT r.id AS record_id, r.symbol, r.fiscal_year, r.cash_pct, r.bonus_pct, r.bookclose_date, r.event_date AS agm_date,
       a.published_date AS announced_date, least(a.published_date, r.bookclose_date) AS knowledge_date,
       CASE WHEN a.id IS NULL THEN 'bookclose' WHEN r.bookclose_date IS NULL OR a.published_date <= r.bookclose_date THEN 'agm_announcement'
            ELSE 'bookclose' END AS knowledge_basis
FROM company_event_records r
LEFT JOIN LATERAL (
    SELECT c.id, c.published_date FROM corporate_announcements c
    WHERE c.symbol = r.symbol AND c.title ~* '(annual general meeting|\mAGM\M)'
      AND c.published_date <= r.event_date AND c.published_date >= r.event_date - 90
    ORDER BY c.published_date LIMIT 1
) a ON true
WHERE r.record_type = 'agm' AND (r.cash_pct IS NOT NULL OR r.bonus_pct IS NOT NULL);

CREATE TABLE IF NOT EXISTS sentiment_observations (
    id              BIGSERIAL PRIMARY KEY,
    series          VARCHAR(60) NOT NULL,
    period_start    DATE,
    period_end      DATE NOT NULL,
    value           NUMERIC NOT NULL,
    unit            VARCHAR(30) NOT NULL,
    published_date  DATE NOT NULL,
    date_basis      VARCHAR(60) NOT NULL,
    source_url      TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '',
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (series, period_end, source_url)
);
"""

APPEND_ONLY = ("corporate_announcements", "archive_documents", "news_articles", "report_field_values",
               "policy_events", "sentiment_observations", "company_event_records")


def apply(engine: Engine) -> list[str]:
    from src.database.holdout_guard import apply_policies

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(820261010)"))
        connection.connection.cursor().execute(DDL)
        connection.execute(text("DROP TRIGGER IF EXISTS news_symbol_mentions_append_only ON news_symbol_mentions"))
        for table in APPEND_ONLY:
            connection.execute(text(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}"))
            connection.execute(text(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                                    "FOR EACH ROW EXECUTE FUNCTION archive_reject_change()"))
    with engine.connect() as connection:
        present = {r[0] for r in connection.execute(text(
            "SELECT tablename FROM pg_policies WHERE policyname = 'holdout_guard' AND tablename = ANY(:t)"), {"t": list(DATE_COLUMNS)})}
    if present == set(DATE_COLUMNS):
        return sorted(present)
    return apply_policies(engine)


def store_raw(source: str, payload: bytes, suffix: str) -> tuple[str, Path]:
    digest = hashlib.sha256(payload).hexdigest()
    folder = RAW_ROOT / source / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}{suffix}"
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
    return digest, path


def mark(engine: Engine, collector: str, key: str, status: str, rows: int = 0, detail: str = "") -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO archive_progress (collector, item_key, status, rows_added, detail) VALUES (:c, :k, :s, :r, :d) "
                 "ON CONFLICT (collector, item_key) DO UPDATE SET status = EXCLUDED.status, rows_added = archive_progress.rows_added + EXCLUDED.rows_added, "
                 "detail = EXCLUDED.detail, updated_at = now()"),
            {"c": collector, "k": key[:300], "s": status, "r": rows, "d": detail[:2000]},
        )


def finished(engine: Engine, collector: str) -> set[str]:
    with engine.connect() as connection:
        return {r[0] for r in connection.execute(
            text("SELECT item_key FROM archive_progress WHERE collector = :c AND status IN ('done', 'not_found')"), {"c": collector})}


if __name__ == "__main__":
    from src.database.connection import engine

    print("policies:", apply(engine))
