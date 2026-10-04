from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.backtest.ledger import DatabaseLedger, variant_fingerprint
from src.collectors import store as text_store
from src.collectors.mentions import load_book
from src.collectors.social import NotConfigured
from src.league.bots import AVOID, BUY, Bot
from src.league.leaderboard import leaderboard
from src.league.run import grade_matured, load_live_graded, penalty_tests, write_calls, write_leaderboard
from src.scorecard import spec, v2
from src.scorecard.daily import NPT, build_state, entry_deadline, quarantined_symbols
from src.scorecard.ledger import feature_hash
from src.scorecard.situations import labels_for, situation_matrix
from src.tips import sources, store
from src.tips.parser import PARSER_VERSION, extract

logger = logging.getLogger(__name__)

FAMILY = "public_tips_v1"
DECLARED_AT = "2026-10-04T11:08:40+05:45"
TRACKER_START = date(2026, 10, 4)
TIP_SOURCES = ("tip_youtube_video", "tip_web_page", "tip_manual")
AGGREGATE = "tips_all"
PROMOTED = "tips_promoted_avoid"
HORIZONS = (5, 10, 20)
TARGET_HORIZONS = (5, 10, 20)
MARKET_OPEN = time(11, 0)

HYPOTHESES: tuple[dict[str, Any], ...] = (
    {"id": "T1_public_buy_tips", "strategy": AGGREGATE, "version": f"{PARSER_VERSION}-buy", "side": BUY, "horizons": list(HORIZONS),
     "rule": "every public buy tip parsed by rule parser p1 from the tracked sources, one call per symbol and signal date"},
    {"id": "T2_public_sell_tips", "strategy": AGGREGATE, "version": f"{PARSER_VERSION}-sell", "side": AVOID, "horizons": list(HORIZONS),
     "rule": "every public sell, exit or avoid tip parsed by p1, graded as an avoid observation"},
    {"id": "A1_promoted_stocks_avoid", "strategy": PROMOTED, "version": PARSER_VERSION, "side": AVOID, "horizons": list(HORIZONS),
     "rule": "a stock with at least one public buy tip known before the call deadline is an avoid observation for that signal date"},
)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unnamed"


def channel_strategy(platform: str, channel: str) -> str:
    return f"tip_{platform}_{slug(channel)}"[:80]


def side_of(strategy: str, version: str) -> str:
    return AVOID if version.endswith("-sell") or strategy == PROMOTED else BUY


def signal_date_for(knowledge: datetime, sessions: list[date]) -> date | None:
    moment = knowledge.astimezone(NPT)
    for position, session in enumerate(sessions):
        if datetime.combine(session, MARKET_OPEN, tzinfo=NPT) > moment:
            return sessions[position - 1] if position > 0 else None
    if sessions and moment.date() <= sessions[-1]:
        return sessions[-1]
    return None


def tip_items(engine: Engine, now: datetime, schema: str = "public") -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT i.id, i.source, i.channel, i.url, i.published_at, i.published_precision, i.first_seen_at, i.raw, "
                f"coalesce(i.title, e.title) AS title, coalesce(i.body, e.body) AS body FROM {schema}.text_items i "
                f"LEFT JOIN {schema}.text_items_ephemeral e ON e.item_id = i.id "
                "WHERE i.source = ANY(:s) AND i.first_seen_at >= :since"
            ),
            {"s": list(TIP_SOURCES), "since": now - timedelta(days=30)},
        ).mappings().all()
    return [dict(r) for r in rows]


def extract_all(engine: Engine, book: Any, now: datetime, schema: str = "public") -> dict[str, int]:
    items = tip_items(engine, now, schema)
    found = inserted = 0
    for item in items:
        tips = extract("\n\n".join(x for x in (item["title"], item["body"]) if x), book)
        found += len(tips)
        item["platform"] = sources.platform_of(item["source"], item["raw"] or {})
        inserted += store.insert_tips(engine, item, tips, schema)
    return {"items_parsed": len(items), "tips_found": found, "tips_inserted": inserted}


def _calls_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["strategy", "model_version", "symbol", "signal_date", "score", "situations", "feature_hash"])


def _call_ids(engine: Engine, keys: list[tuple[str, str, str, date]], schema: str) -> list[int]:
    ids = []
    with engine.connect() as connection:
        for strategy, version, symbol, day in keys:
            row = connection.execute(
                text(f"SELECT id FROM {schema}.scorecard_calls WHERE strategy = :s AND model_version = :v AND symbol = :y AND signal_date = :d"),
                {"s": strategy, "v": version, "y": symbol, "d": day},
            ).first()
            if row is not None:
                ids.append(int(row.id))
    return ids


def write_tips(engine: Engine, state: dict[str, Any], quarantine: Any, now: datetime, dry_run: bool, schema: str = "public") -> dict[str, Any]:
    panel = state["panel"]
    sessions = list(panel.sessions)
    index = {d: i for i, d in enumerate(sessions)}
    situations = situation_matrix(state["market"], state["inputs"]["index"], state["inputs"]["rates"], state["mergers"])
    counts: dict[str, int] = {"pending": 0}
    with engine.connect() as connection:
        exists = connection.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.public_tips"}).scalar()
    if exists is None:
        return {"pending": 0, "note": "public_tips does not exist yet"}
    for tip in store.open_tips(engine, schema):
        basis = tip["posted_at"] if tip["posted_precision"] in ("second", "minute") and tip["posted_at"] is not None else tip["first_seen_at"]
        signal = signal_date_for(basis, sessions)
        if signal is None:
            counts["pending"] += 1
            continue
        if now >= entry_deadline(signal):
            event, keys = "outside_write_window", []
        elif tip["symbol"] not in panel.row or not spec.valid_symbol(tip["symbol"]):
            event, keys = "unknown_symbol", []
        elif tip["symbol"] in set(quarantine):
            event, keys = "quarantined", []
        else:
            labels = labels_for(situations, int(panel.row[tip["symbol"]]), index[signal])
            side = tip["direction"]
            keys = [(channel_strategy(tip["platform"], tip["channel"]), f"{PARSER_VERSION}-{side}", tip["symbol"], signal),
                    (AGGREGATE, f"{PARSER_VERSION}-{side}", tip["symbol"], signal)]
            if side == "buy":
                keys.append((PROMOTED, PARSER_VERSION, tip["symbol"], signal))
            rows = [{"strategy": s, "model_version": v, "symbol": y, "signal_date": d, "score": None, "situations": labels,
                     "feature_hash": feature_hash({"tip_id": tip["id"], "strategy": s, "version": v, "symbol": y, "date": d,
                                                   "segment": tip["segment_sha256"], "situations": labels})}
                    for s, v, y, d in keys]
            if dry_run:
                event = "written"
            else:
                first = write_calls(engine, _calls_frame(rows[:1]), now, schema)
                write_calls(engine, _calls_frame(rows[1:]), now, schema)
                event = "written" if first["inserted"] else "duplicate_same_day"
        counts[event] = counts.get(event, 0) + 1
        if not dry_run:
            store.record_event(engine, int(tip["id"]), event, signal, _call_ids(engine, keys, schema) if keys else [], schema=schema)
    return counts


def tracked_pairs(engine: Engine, schema: str = "public") -> set[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"SELECT DISTINCT strategy, model_version FROM {schema}.scorecard_calls WHERE mode = 'live' "
                 "AND (strategy LIKE 'tip\\_%' OR strategy = ANY(:a))"),
            {"a": [AGGREGATE, PROMOTED]},
        ).all()
    return {(r[0], r[1]) for r in rows}


def target_report(engine: Engine, state: dict[str, Any], schema: str = "public") -> dict[str, Any]:
    panel, market = state["panel"], state["market"]
    index = {d: i for i, d in enumerate(panel.sessions)}
    last = len(panel.sessions) - 1
    with engine.connect() as connection:
        tips = connection.execute(
            text(f"SELECT t.platform, t.channel, t.symbol, t.target, t.stop, e.signal_date FROM {schema}.public_tips t "
                 f"JOIN {schema}.public_tip_events e ON e.tip_id = t.id WHERE e.event = 'written' AND t.direction = 'buy' "
                 "AND (t.target IS NOT NULL OR t.stop IS NOT NULL)")
        ).mappings().all()
    out: dict[str, dict[str, Any]] = {}
    for tip in tips:
        key = channel_strategy(tip["platform"], tip["channel"])
        r, t = panel.row.get(tip["symbol"]), index.get(tip["signal_date"])
        if r is None or t is None:
            continue
        for h in TARGET_HORIZONS:
            cell = out.setdefault(key, {}).setdefault(str(h), {"target_first": 0, "stop_first": 0, "neither": 0, "pending": 0, "excluded": 0})
            start, end = t + 1, t + h
            if end > last:
                cell["pending"] += 1
                continue
            if market.action_mask[r, start : end + 1].any():
                cell["excluded"] += 1
                continue
            outcome = "neither"
            for c in range(start, end + 1):
                if np.isnan(panel.close[r, c]):
                    continue
                if tip["stop"] is not None and panel.low[r, c] <= tip["stop"]:
                    outcome = "stop_first"
                    break
                if tip["target"] is not None and panel.high[r, c] >= tip["target"]:
                    outcome = "target_first"
                    break
            cell[outcome] += 1
    return out


def boards(engine: Engine, state: dict[str, Any], schema: str = "public") -> dict[str, Any]:
    pairs = tracked_pairs(engine, schema)
    graded = load_live_graded(engine, schema, sorted({p[0] for p in pairs})) if pairs else pd.DataFrame()
    live = [d for d in state["panel"].sessions if d >= TRACKER_START]
    aggregates = [Bot(s, v, side_of(s, v), HORIZONS, "") for s, v in sorted(pairs) if not s.startswith("tip_")]
    channels = [Bot(s, v, side_of(s, v), HORIZONS, "") for s, v in sorted(pairs) if s.startswith("tip_")]
    return {
        "aggregate": leaderboard(graded, live, penalty_tests(engine), aggregates, HORIZONS) if aggregates else {},
        "channels": leaderboard(graded, live, max(1, len(channels)) * len(spec.SITUATIONS) * len(spec.HORIZONS), channels, HORIZONS)
        if channels else {},
        "channel_penalty_note": "per-channel verdicts are descriptive: K = tracked channel versions x 104, not a claim for any system",
    }


def collect(engine: Engine, book: Any, dry_run: bool, schema: str = "public") -> list[dict[str, Any]]:
    config = sources.load_config()
    reports = []
    for name, fn in (("tip_youtube", lambda: sources.youtube_videos(book, config.get("youtube_channels") or [])),
                     ("tip_web", lambda: sources.web_pages(book, config.get("web_pages") or []))):
        if name == "tip_youtube" and not config.get("youtube_channels"):
            reports.append({"source": name, "status": "not_configured", "reason": "no youtube_channels in config/tip_sources.json"})
            continue
        if name == "tip_web" and not config.get("web_pages"):
            reports.append({"source": name, "status": "not_configured", "reason": "no web_pages in config/tip_sources.json"})
            continue
        started = datetime.now(tz=timezone.utc)
        try:
            items, detail = fn()
        except NotConfigured as error:
            reports.append({"source": name, "status": "not_configured", "reason": str(error)})
            continue
        inserted = 0 if dry_run else text_store.insert_items(engine, items, sources.VERSION, schema)
        if not dry_run:
            text_store.record_run(engine, name, started, "ok", len(items), inserted, 0, detail, sources.VERSION, schema)
        reports.append({"source": name, "status": "ok", "items": len(items), "inserted": inserted, "detail": detail})
    reports.append({"source": "telegram", "status": "blocked", "reason": sources.TELEGRAM_REASON})
    return reports


def live_state() -> tuple[dict[str, Any], date]:
    from src.database.connection import engine
    from src.database.holdout_guard import allow

    with engine.connect() as connection:
        latest = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one()
    with allow("live_ledger"):
        return build_state(latest), latest


def declare(engine: Engine) -> dict[str, Any]:
    ledger = DatabaseLedger()
    out = []
    for hypothesis in HYPOTHESES:
        params = {"family": FAMILY, "declared_at": DECLARED_AT, "parser_version": PARSER_VERSION, "tracker_start": TRACKER_START.isoformat(),
                  "protocol": v2.PROTOCOL_VERSION, "edge_min": v2.GATE_EDGE, "min_windows": v2.MIN_WINDOWS, **hypothesis}
        ledger.register_variant(FAMILY, params, f"{FAMILY} {hypothesis['id']} declared {DECLARED_AT}")
        ledger.register_variant(v2.VARIANT_FAMILY, {"protocol": v2.PROTOCOL_VERSION, "strategy": hypothesis["strategy"],
                                                    "version": hypothesis["version"], **params},
                                f"{v2.PROTOCOL_VERSION} strategy {hypothesis['strategy']} {hypothesis['version']}")
        out.append({"id": hypothesis["id"], "fingerprint": variant_fingerprint(params)})
    return {"declared_at": DECLARED_AT, "hypotheses": out}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Public tip tracker: collect public tips, parse them, write them to the ledger, grade them")
    sub = parser.add_subparsers(dest="command", required=True)
    cycle = sub.add_parser("cycle")
    cycle.add_argument("--dry-run", action="store_true")
    cycle.add_argument("--no-collect", action="store_true")
    cycle.add_argument("--grade", action="store_true")
    add = sub.add_parser("add")
    add.add_argument("--platform", required=True, choices=sources.PLATFORMS)
    add.add_argument("--channel", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--posted-at", required=True, type=datetime.fromisoformat)
    add.add_argument("--text", required=True)
    add.add_argument("--consent-ref")
    sub.add_parser("declare")
    args = parser.parse_args()
    from src.database.connection import engine

    now = datetime.now(tz=NPT)
    if args.command == "declare":
        store.apply_schema(engine)
        print(json.dumps(declare(engine), indent=2))
        return
    book = load_book(engine)
    if args.command == "add":
        store.apply_schema(engine)
        try:
            item = sources.manual_item(book, args.platform, args.channel, args.url, args.posted_at, args.text, args.consent_ref)
        except PermissionError as error:
            logger.error("%s", error)
            sys.exit(6)
        inserted = text_store.insert_items(engine, [item], sources.VERSION)
        print(json.dumps({"inserted": inserted, "symbols": list(item.symbols), "extract": extract_all(engine, book, now)}, indent=2))
        return
    if not args.dry_run:
        store.apply_schema(engine)
    report: dict[str, Any] = {"now": now.isoformat(), "dry_run": args.dry_run}
    report["collect"] = [] if args.no_collect else collect(engine, book, args.dry_run)
    report["extract"] = "dry run: not extracted" if args.dry_run else extract_all(engine, book, now)
    report["parser_version"] = PARSER_VERSION
    with engine.connect() as connection:
        exists = connection.execute(text("SELECT to_regclass('public.public_tips')")).scalar()
    waiting = len(store.open_tips(engine)) if exists else 0
    report["open_tips"] = waiting
    if not waiting and not (args.grade and tracked_pairs(engine)):
        report["write"] = "no open tips and nothing to grade: price state not loaded"
        print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
        return
    state, latest = live_state()
    report["latest_session"] = latest.isoformat()
    report["write"] = write_tips(engine, state, quarantined_symbols(engine), now, args.dry_run)
    if args.grade and not args.dry_run:
        report["grading"] = grade_matured(engine, state, False, pairs=tracked_pairs(engine)) if tracked_pairs(engine) else "no tip calls"
        board = boards(engine, state)
        report["leaderboard_rows_written"] = sum(write_leaderboard(engine, b, latest, table="tip_leaderboard")
                                                 for b in (board["aggregate"], board["channels"]) if b)
        report["targets"] = target_report(engine, state)
    print(json.dumps(report, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
