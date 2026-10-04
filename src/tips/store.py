from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.tips.parser import PARSER_VERSION, Tip

EVENTS = ("written", "outside_write_window", "unknown_symbol", "quarantined", "duplicate_same_day")

DDL = """
CREATE TABLE IF NOT EXISTS {schema}.public_tips (
    id               BIGSERIAL PRIMARY KEY,
    text_item_id     BIGINT NOT NULL REFERENCES {schema}.text_items (id),
    platform         VARCHAR(20) NOT NULL,
    channel          VARCHAR(200) NOT NULL,
    url              TEXT,
    posted_at        TIMESTAMPTZ,
    posted_precision VARCHAR(10) NOT NULL,
    first_seen_at    TIMESTAMPTZ NOT NULL,
    knowledge_at     TIMESTAMPTZ NOT NULL,
    symbol           VARCHAR(20) NOT NULL,
    direction        VARCHAR(4) NOT NULL CHECK (direction IN ('buy', 'sell')),
    raw_direction    VARCHAR(10) NOT NULL,
    entry_low        DOUBLE PRECISION,
    entry_high       DOUBLE PRECISION,
    target           DOUBLE PRECISION,
    stop             DOUBLE PRECISION,
    segment_sha256   CHAR(64) NOT NULL,
    parser_version   VARCHAR(10) NOT NULL,
    extracted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, channel, symbol, direction, segment_sha256, parser_version)
);

CREATE TABLE IF NOT EXISTS {schema}.public_tip_events (
    id           BIGSERIAL PRIMARY KEY,
    tip_id       BIGINT NOT NULL REFERENCES {schema}.public_tips (id),
    event        VARCHAR(30) NOT NULL,
    signal_date  DATE,
    call_ids     BIGINT[] NOT NULL DEFAULT '{{}}',
    detail       TEXT,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tip_id)
);

CREATE TABLE IF NOT EXISTS {schema}.tip_leaderboard (
    id            BIGSERIAL PRIMARY KEY,
    as_of         DATE NOT NULL,
    bot           VARCHAR(80) NOT NULL,
    bot_version   VARCHAR(20) NOT NULL,
    horizon       SMALLINT NOT NULL,
    rank          SMALLINT NOT NULL,
    metrics       JSONB NOT NULL,
    code_commit   VARCHAR(40) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (as_of, bot, bot_version, horizon)
);

DROP TRIGGER IF EXISTS public_tips_immutable ON {schema}.public_tips;
CREATE TRIGGER public_tips_immutable BEFORE UPDATE OR DELETE ON {schema}.public_tips
    FOR EACH ROW EXECUTE FUNCTION {schema}.text_items_reject_change();
DROP TRIGGER IF EXISTS public_tip_events_immutable ON {schema}.public_tip_events;
CREATE TRIGGER public_tip_events_immutable BEFORE UPDATE OR DELETE ON {schema}.public_tip_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.text_items_reject_change();
DROP TRIGGER IF EXISTS tip_leaderboard_immutable ON {schema}.tip_leaderboard;
CREATE TRIGGER tip_leaderboard_immutable BEFORE UPDATE OR DELETE ON {schema}.tip_leaderboard
    FOR EACH ROW EXECUTE FUNCTION {schema}.text_items_reject_change();
"""


def apply_schema(engine: Engine, schema: str = "public") -> None:
    from src.collectors.store import apply_schema as apply_text_schema
    from src.league.run import apply_league_schema

    apply_text_schema(engine, schema)
    apply_league_schema(engine, schema)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(DDL.format(schema=schema))
        raw.commit()
    finally:
        raw.close()


def insert_tips(engine: Engine, item: dict[str, Any], tips: Iterable[Tip], schema: str = "public") -> int:
    knowledge = max(t for t in (item["first_seen_at"], item["published_at"]) if t is not None)
    inserted = 0
    with engine.begin() as connection:
        for tip in tips:
            row = connection.execute(
                text(
                    f"INSERT INTO {schema}.public_tips (text_item_id, platform, channel, url, posted_at, posted_precision, first_seen_at, "
                    "knowledge_at, symbol, direction, raw_direction, entry_low, entry_high, target, stop, segment_sha256, parser_version) "
                    "VALUES (:i, :p, :c, :u, :pa, :pp, :fs, :k, :s, :d, :rd, :el, :eh, :t, :st, :h, :v) "
                    "ON CONFLICT (platform, channel, symbol, direction, segment_sha256, parser_version) DO NOTHING RETURNING id"
                ),
                {"i": item["id"], "p": item["platform"], "c": item["channel"], "u": item["url"], "pa": item["published_at"],
                 "pp": item["published_precision"], "fs": item["first_seen_at"], "k": knowledge, "s": tip.symbol, "d": tip.direction,
                 "rd": tip.raw_direction, "el": tip.entry_low, "eh": tip.entry_high, "t": tip.target, "st": tip.stop,
                 "h": tip.segment_sha256, "v": PARSER_VERSION},
            ).first()
            inserted += int(row is not None)
    return inserted


def record_event(engine: Engine, tip_id: int, event: str, signal_date: Any, call_ids: list[int], detail: str = "",
                 schema: str = "public") -> bool:
    if event not in EVENTS:
        raise ValueError(event)
    with engine.begin() as connection:
        row = connection.execute(
            text(f"INSERT INTO {schema}.public_tip_events (tip_id, event, signal_date, call_ids, detail) VALUES (:t, :e, :d, :c, :x) "
                 "ON CONFLICT (tip_id) DO NOTHING RETURNING id"),
            {"t": tip_id, "e": event, "d": signal_date, "c": call_ids, "x": detail},
        ).first()
    return row is not None


def open_tips(engine: Engine, schema: str = "public") -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"SELECT t.* FROM {schema}.public_tips t LEFT JOIN {schema}.public_tip_events e ON e.tip_id = t.id "
                 "WHERE e.id IS NULL ORDER BY t.id")
        ).mappings().all()
    return [dict(r) for r in rows]
