from __future__ import annotations

import argparse
import bisect
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.backtest import event_study as es

logger = logging.getLogger(__name__)

STEP_TOLERANCE = 0.005
HALT_GAP_SESSIONS = 20
MATCH_WINDOW_SESSIONS = 2
DETECTABLE_DROP = 0.15
RIGHTS_PRICE = es.RIGHTS_SUBSCRIPTION_PRICE
PRE_HOLDOUT_END = date(2025, 9, 29)
DEFAULT_REPORT = Path("docs/price_integrity.json")
SCRAPE_DELAY_SECONDS = 3.0
SHARESANSAR_SOURCE = "sharesansar"
MEROLAGANI_SOURCE = "merolagani"
SHARESANSAR_URL = "https://www.sharesansar.com/company/{slug}"
MEROLAGANI_URL = "https://merolagani.com/CompanyDetail.aspx?symbol={symbol}"

TABLES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS corporate_action_sources (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        action_date DATE NOT NULL,
        action_type TEXT NOT NULL,
        ratio_or_amount NUMERIC(14,4) NOT NULL,
        source TEXT NOT NULL,
        source_url TEXT NOT NULL,
        source_detail TEXT NOT NULL,
        verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (symbol, action_date, action_type, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_quarantine (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        step_date DATE NOT NULL,
        raw_move DOUBLE PRECISION NOT NULL,
        reason TEXT NOT NULL,
        sources_checked TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (symbol, step_date)
    )
    """,
)

RESOLVED = "resolved_by_action"
HALT = "halt_resumption"
UNRESOLVED = "unresolved"
MISMATCH = "action_mismatch"
OVERSHOOT = "adjustment_overshoot"
UNRESOLVED_KINDS = (UNRESOLVED, MISMATCH, OVERSHOOT)


def band(day: date) -> float:
    return es.circuit_limit(day)


def is_step(move: float, day: date) -> bool:
    return abs(move) > band(day) + STEP_TOLERANCE


def detect_steps(
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    sessions: Sequence[date],
) -> pd.DataFrame:
    effects = es._action_effects(actions, sessions)
    col = {day: i for i, day in enumerate(sessions)}
    records = []
    for symbol, group in prices.sort_values(["symbol", "date"]).groupby("symbol", sort=False):
        days = group["date"].to_numpy()
        closes = group["close"].to_numpy(dtype=float)
        opens = group["open"].to_numpy(dtype=float) if "open" in group else np.full(len(group), np.nan)
        idx = [col.get(d) for d in days]
        symbol_effects = effects.get(symbol, {})
        for k in range(1, len(days)):
            p, c = idx[k - 1], idx[k]
            if p is None or c is None or closes[k - 1] <= 0:
                continue
            raw = closes[k] / closes[k - 1] - 1.0
            entries = [e for s in range(p + 1, c + 1) for e in symbol_effects.get(s, [])]
            adjusted = es._apply_actions(closes[k], entries) / closes[k - 1] - 1.0
            base = adjusted_base(closes[k - 1], entries)
            base_move = closes[k] / base - 1.0 if base > 0 else adjusted
            day = sessions[c]
            raw_step = is_step(raw, day)
            adjusted_step = is_step(adjusted, day) and is_step(base_move, day)
            open_step = False
            if day >= es.FIRST_REAL_OPEN_SESSION and opens[k] > 0:
                open_adjusted = es._apply_actions(opens[k], entries) / closes[k - 1] - 1.0
                open_base = opens[k] / base - 1.0 if base > 0 else open_adjusted
                open_step = is_step(open_adjusted, day) and is_step(open_base, day)
            if not raw_step and not adjusted_step and not open_step:
                continue
            gap = c - p
            if entries and not adjusted_step and not open_step:
                kind = RESOLVED
            elif entries and raw_step:
                kind = MISMATCH
            elif entries:
                kind = OVERSHOOT
            elif gap >= HALT_GAP_SESSIONS:
                kind = HALT
            else:
                kind = UNRESOLVED
            records.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "session_index": c,
                    "previous_date": sessions[p],
                    "gap_sessions": gap,
                    "previous_close": float(closes[k - 1]),
                    "close": float(closes[k]),
                    "raw_move": float(raw),
                    "adjusted_move": float(adjusted),
                    "base_move": float(base_move),
                    "open_step": bool(open_step),
                    "actions_in_gap": ";".join(f"{kind_}:{value:g}" for kind_, value in entries),
                    "kind": kind,
                }
            )
    return pd.DataFrame.from_records(
        records,
        columns=[
            "symbol", "date", "session_index", "previous_date", "gap_sessions", "previous_close", "close",
            "raw_move", "adjusted_move", "base_move", "open_step", "actions_in_gap", "kind",
        ],
    )


def unresolved_dates(steps: pd.DataFrame) -> dict[str, list[date]]:
    chosen = steps[steps["kind"].isin(UNRESOLVED_KINDS)]
    out: dict[str, list[date]] = {}
    for symbol, group in chosen.groupby("symbol"):
        out[symbol] = sorted(group["date"])
    return out


def mark_unresolved(panel: Any, prices: pd.DataFrame, actions: pd.DataFrame) -> int:
    steps = detect_steps(prices, actions, panel.sessions)
    mask = unresolved_mask(steps, panel.symbols, len(panel.sessions))
    added = int((mask & ~panel.corrupt).sum())
    panel.corrupt[mask] = True
    return added


def adjusted_base(previous_close: float, entries: Sequence[tuple[str, float]]) -> float:
    if not entries:
        return previous_close
    extra = es._apply_actions(0.0, entries)
    shares = es._apply_actions(1.0, entries) - extra
    return (previous_close - extra) / shares


def unresolved_mask(steps: pd.DataFrame, symbols: Sequence[str], n_sessions: int) -> np.ndarray:
    row = {s: i for i, s in enumerate(symbols)}
    mask = np.zeros((len(symbols), n_sessions), dtype=bool)
    chosen = steps[steps["kind"].isin(UNRESOLVED_KINDS)]
    for symbol, c in zip(chosen["symbol"], chosen["session_index"]):
        r = row.get(symbol)
        if r is not None and 0 <= c < n_sessions:
            mask[r, c] = True
    return mask


def theoretical_drop(kind: str, value: float, previous_close: float) -> float | None:
    if kind == "BONUS":
        return 1.0 - 1.0 / (1.0 + value / 100.0)
    if kind == "RIGHT":
        q = value / 100.0
        if previous_close <= 0:
            return None
        terp = (previous_close + RIGHTS_PRICE * q) / (1.0 + q)
        return 1.0 - terp / previous_close
    return None


def calibrate(prices: pd.DataFrame, actions: pd.DataFrame, sessions: Sequence[date]) -> dict[str, Any]:
    with_actions = set(actions["symbol"])
    subset = prices[prices["symbol"].isin(with_actions)]
    no_adjust = actions.iloc[0:0]
    raw_steps = detect_steps(subset, no_adjust, sessions)
    down = raw_steps[raw_steps["raw_move"] < -(STEP_TOLERANCE)]
    down = down[[is_step(m, d) for m, d in zip(down["raw_move"], down["date"])]]
    events = actions[actions["action_type"].isin(["BONUS", "RIGHT"])].copy()
    events["ex_index"] = [bisect.bisect_left(sessions, d) for d in events["action_date"]]
    events = events[events["ex_index"] < len(sessions)]
    traded_index: dict[str, list[int]] = {}
    col = {d: i for i, d in enumerate(sessions)}
    close_by: dict[str, dict[int, float]] = {}
    for symbol, group in subset.groupby("symbol"):
        positions = sorted(col[d] for d in group["date"] if d in col)
        traded_index[symbol] = positions
        close_by[symbol] = {col[d]: float(c) for d, c in zip(group["date"], group["close"]) if d in col}
    event_rows = []
    for event in events.itertuples(index=False):
        positions = traded_index.get(event.symbol, [])
        k = bisect.bisect_left(positions, event.ex_index)
        if k == 0 or k >= len(positions):
            continue
        first_trade = positions[k]
        previous = positions[k - 1]
        prev_close = close_by[event.symbol][previous]
        drop = theoretical_drop(event.action_type, float(event.ratio_or_amount), prev_close)
        detected = down[
            (down["symbol"] == event.symbol)
            & (down["session_index"] >= first_trade - MATCH_WINDOW_SESSIONS)
            & (down["session_index"] <= first_trade + MATCH_WINDOW_SESSIONS)
        ]
        realized = close_by[event.symbol][first_trade] / prev_close
        event_rows.append(
            {
                "symbol": event.symbol,
                "type": event.action_type,
                "value": float(event.ratio_or_amount),
                "ex_index": int(first_trade),
                "theoretical_drop": drop,
                "detectable": drop is not None and drop >= DETECTABLE_DROP,
                "detected": not detected.empty,
                "implied_bonus_pct": (1.0 / realized - 1.0) * 100.0,
            }
        )
    ev = pd.DataFrame(event_rows)
    matched = set()
    for symbol, group in ev.groupby("symbol"):
        for ex in group["ex_index"]:
            hits = down[(down["symbol"] == symbol) & (down["session_index"].between(ex - MATCH_WINDOW_SESSIONS, ex + MATCH_WINDOW_SESSIONS))]
            matched.update(hits.index.tolist())
    precision = len(matched) / len(down) if len(down) else None
    result: dict[str, Any] = {
        "symbols_with_actions": len(with_actions),
        "stored_actions": int(len(actions)),
        "bonus_right_events_with_trades_around": int(len(ev)),
        "downward_steps_detected": int(len(down)),
        "downward_steps_matched_to_bonus_or_right": len(matched),
        "precision": None if precision is None else round(precision, 4),
        "recall_all_events": round(float(ev["detected"].mean()), 4) if len(ev) else None,
        "recall_detectable_events": round(float(ev.loc[ev["detectable"], "detected"].mean()), 4) if ev["detectable"].any() else None,
        "detectable_events": int(ev["detectable"].sum()),
        "recall_by_type": {
            t: round(float(g["detected"].mean()), 4) for t, g in ev.groupby("type")
        },
        "recall_detectable_by_type": {
            t: round(float(g["detected"].mean()), 4) for t, g in ev[ev["detectable"]].groupby("type")
        },
    }
    bonus = ev[(ev["type"] == "BONUS") & ev["detected"]]
    if len(bonus):
        error = bonus["implied_bonus_pct"] - bonus["value"]
        result["bonus_ratio_recovery"] = {
            "detected_bonus_events": int(len(bonus)),
            "median_abs_error_pct_points": round(float(error.abs().median()), 2),
            "share_within_2_points": round(float((error.abs() <= 2).mean()), 4),
            "share_within_5_points": round(float((error.abs() <= 5).mean()), 4),
            "p90_abs_error_pct_points": round(float(error.abs().quantile(0.9)), 2),
        }
    unmatched = down.drop(index=list(matched))
    result["unmatched_downward_steps_examples"] = unmatched.head(10)[["symbol", "date", "raw_move", "gap_sessions"]].astype(str).to_dict("records")
    return result


def load_inputs(end: date = PRE_HOLDOUT_END) -> dict[str, Any]:
    from src.backtest.event_data import DEVELOPMENT_START
    from src.database.holdout_guard import engine

    with engine.connect() as connection:
        prices = pd.read_sql(
            text(
                "SELECT p.symbol, p.date, p.open, p.close FROM daily_prices p JOIN companies c "
                "ON c.symbol = p.symbol AND c.instrument_type = 'Equity' WHERE p.date >= :s AND p.date <= :e"
            ),
            connection,
            params={"s": DEVELOPMENT_START, "e": end},
        )
        sessions = [
            r[0]
            for r in connection.execute(
                text("SELECT DISTINCT date FROM daily_prices WHERE date >= :s AND date <= :e ORDER BY 1"),
                {"s": DEVELOPMENT_START, "e": end},
            )
        ]
        actions = pd.read_sql(
            text(
                "SELECT symbol, action_date, action_type::text AS action_type, ratio_or_amount::float AS ratio_or_amount "
                "FROM corporate_actions WHERE action_date <= :e"
            ),
            connection,
            params={"e": end},
        )
        companies = pd.read_sql(text("SELECT symbol, status, instrument_type FROM companies"), connection)
    from src.scorecard.spec import valid_symbol

    prices = prices[prices["symbol"].map(valid_symbol)]
    prices["open"] = prices["open"].astype(float)
    prices["close"] = prices["close"].astype(float)
    return {"prices": prices, "sessions": sessions, "actions": actions, "companies": companies}


def summarize(steps: pd.DataFrame, companies: pd.DataFrame) -> dict[str, Any]:
    status = dict(zip(companies["symbol"], companies["status"]))
    steps = steps.assign(status=steps["symbol"].map(status))
    out: dict[str, Any] = {"steps": int(len(steps)), "by_kind": steps["kind"].value_counts().to_dict()}
    for name, group in steps.groupby("status"):
        out[f"status_{name}"] = {
            "steps": int(len(group)),
            "symbols": int(group["symbol"].nunique()),
            "by_kind": group["kind"].value_counts().to_dict(),
            "unresolved_symbols": int(group.loc[group["kind"].isin(UNRESOLVED_KINDS), "symbol"].nunique()),
        }
    out["unresolved_by_direction"] = {
        "down": int(((steps["kind"].isin(UNRESOLVED_KINDS)) & (steps["raw_move"] < 0)).sum()),
        "up": int(((steps["kind"].isin(UNRESOLVED_KINDS)) & (steps["raw_move"] >= 0)).sum()),
    }
    out["unresolved_by_year"] = (
        steps[steps["kind"].isin(UNRESOLVED_KINDS)].groupby(pd.to_datetime(steps["date"]).dt.year).size().astype(int).to_dict()
    )
    return out


def recover_delisted(symbols: Iterable[str], delay: float = SCRAPE_DELAY_SECONDS) -> dict[str, Any]:
    from src.pipeline.db_writers import insert_new_corporate_actions
    from src.scrapers.corporate_actions_scraper import get_corporate_actions

    report: dict[str, Any] = {"attempted": 0, "found_symbols": 0, "rows_found": 0, "rows_inserted": 0, "errors": 0, "per_symbol": {}}
    for symbol in symbols:
        report["attempted"] += 1
        try:
            rows = get_corporate_actions(symbol)
        except Exception as error:
            report["errors"] += 1
            report["per_symbol"][symbol] = f"error: {type(error).__name__}"
            time.sleep(delay)
            continue
        if rows:
            report["found_symbols"] += 1
            report["rows_found"] += len(rows)
            inserted, _ = insert_new_corporate_actions(rows)
            report["rows_inserted"] += inserted
        report["per_symbol"][symbol] = len(rows)
        time.sleep(delay)
    return report


def recent_unresolved(steps: pd.DataFrame, sessions: Sequence[date], lookback: int) -> pd.DataFrame:
    if not sessions:
        return steps.iloc[0:0]
    cutoff = sessions[max(0, len(sessions) - lookback)]
    return steps[(steps["kind"].isin(UNRESOLVED_KINDS)) & (steps["date"] >= cutoff)]


def check_recent(
    steps: pd.DataFrame,
    sessions: Sequence[date],
    lookback: int,
    quarantined: Iterable[tuple[str, date]] = (),
) -> list[dict[str, Any]]:
    known = set(quarantined)
    recent = recent_unresolved(steps, sessions, lookback)
    recent = recent[[(s, d) not in known for s, d in zip(recent["symbol"], recent["date"])]]
    return recent[["symbol", "date", "raw_move", "adjusted_move", "kind"]].astype(str).to_dict("records")


def apply_tables(connection: Any) -> None:
    for statement in TABLES_DDL:
        connection.execute(text(statement))


def load_quarantine(connection: Any) -> pd.DataFrame:
    exists = connection.execute(text("SELECT to_regclass('public.price_quarantine')")).scalar()
    if exists is None:
        return pd.DataFrame(columns=["symbol", "step_date", "raw_move", "reason", "sources_checked"])
    return pd.read_sql(
        text("SELECT symbol, step_date, raw_move, reason, sources_checked FROM price_quarantine ORDER BY symbol, step_date"),
        connection,
    )


def parse_merolagani_actions(html: str) -> dict[str, list[tuple[float, str]]]:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
    out: dict[str, list[tuple[float, str]]] = {"RIGHT": [], "BONUS": []}
    right = re.search(r"Right Share .*?# Value Fiscal Year(.*?)(?:30-Day Avg Volume|$)", body)
    if right:
        for old, new, fy in re.findall(r"\d+\.\s*([\d.]+):([\d.]+)\s*\(FY:\s*([\d-]+)\)", right.group(1)):
            if float(old) > 0:
                out["RIGHT"].append((float(new) / float(old) * 100.0, fy))
    bonus = re.search(r"% Bonus ([^#]*)# Value Fiscal Year(.*?)Right Share", body)
    if bonus:
        listed = re.findall(r"\d+\.\s*([\d.]+)%\s*\(FY:\s*([\d-]+)\)", bonus.group(2))
        headline = re.findall(r"([\d.]+)\s*\(FY:\s*([\d-]+)\)", bonus.group(1))
        for value, fy in listed + headline:
            if float(value) > 0 and (float(value), fy) not in out["BONUS"]:
                out["BONUS"].append((float(value), fy))
    return out


def fetch_merolagani_actions(symbol: str) -> dict[str, list[tuple[float, str]]]:
    from src.scrapers.http_utils import fetch

    return parse_merolagani_actions(fetch(MEROLAGANI_URL.format(symbol=symbol)).text)


def resolve_recent(
    inputs: dict[str, Any],
    lookback: int,
    engine: Any,
    delay: float = SCRAPE_DELAY_SECONDS,
) -> dict[str, Any]:
    from src.pipeline.db_writers import insert_new_corporate_actions
    from src.scrapers.corporate_actions_scraper import get_corporate_actions

    sessions = inputs["sessions"]
    steps = detect_steps(inputs["prices"], inputs["actions"], sessions)
    cutoff = sessions[max(0, len(sessions) - lookback)]
    near = steps[(steps["date"] >= cutoff) & ((steps["kind"] == RESOLVED) | steps["kind"].isin(UNRESOLVED_KINDS))]
    report: dict[str, Any] = {"recent_steps": near[["symbol", "date", "raw_move", "adjusted_move", "base_move", "kind", "actions_in_gap"]].astype(str).to_dict("records"),
                              "sources_recorded": [], "actions_inserted": 0, "quarantined": []}
    merolagani_status: dict[str, str] = {}
    with engine.begin() as connection:
        apply_tables(connection)
    for symbol, group in near.groupby("symbol"):
        sharesansar = get_corporate_actions(symbol)
        time.sleep(delay)
        try:
            merolagani = fetch_merolagani_actions(symbol)
            merolagani_status[symbol] = "checked"
        except Exception as error:
            merolagani = {}
            merolagani_status[symbol] = f"unreachable ({type(error).__name__})"
        time.sleep(delay)
        positions = {int(i) for i in group["session_index"]}
        nearby = [
            row for row in sharesansar
            if row["action_date"] <= PRE_HOLDOUT_END
            and any(abs(bisect.bisect_left(sessions, row["action_date"]) - p) <= HALT_GAP_SESSIONS for p in positions)
        ]
        new_rows = [r for r in nearby if r["action_type"] in ("bonus", "right")]
        if new_rows:
            inserted, _ = insert_new_corporate_actions(new_rows)
            report["actions_inserted"] += inserted
        with engine.begin() as connection:
            for row in nearby:
                kind = row["action_type"].upper()
                value = float(row["ratio_or_amount"])
                connection.execute(
                    text(
                        "INSERT INTO corporate_action_sources (symbol, action_date, action_type, ratio_or_amount, source, source_url, source_detail) "
                        "VALUES (:s, :d, :t, :v, :src, :u, :det) ON CONFLICT DO NOTHING"
                    ),
                    {"s": symbol, "d": row["action_date"], "t": kind, "v": value, "src": SHARESANSAR_SOURCE,
                     "u": SHARESANSAR_URL.format(slug=symbol.lower()),
                     "det": f"{kind.lower()} {value:g}% book close {row['action_date']} fiscal year {row['fiscal_year']}"},
                )
                match = [fy for v, fy in merolagani.get(kind, []) if abs(v - value) < 0.01]
                if match:
                    connection.execute(
                        text(
                            "INSERT INTO corporate_action_sources (symbol, action_date, action_type, ratio_or_amount, source, source_url, source_detail) "
                            "VALUES (:s, :d, :t, :v, :src, :u, :det) ON CONFLICT DO NOTHING"
                        ),
                        {"s": symbol, "d": row["action_date"], "t": kind, "v": value, "src": MEROLAGANI_SOURCE,
                         "u": MEROLAGANI_URL.format(symbol=symbol),
                         "det": f"{kind.lower()} {value:g}% fiscal year {match[0]} (no book-close date on page)"},
                    )
                report["sources_recorded"].append(
                    {"symbol": symbol, "action_date": str(row["action_date"]), "type": kind, "value": value,
                     "sharesansar": True, "merolagani": bool(match)}
                )
    refreshed = load_inputs(sessions[-1])
    after = detect_steps(refreshed["prices"], refreshed["actions"], refreshed["sessions"])
    still = recent_unresolved(after, refreshed["sessions"], lookback)
    with engine.begin() as connection:
        for step in still.itertuples(index=False):
            checked = (
                f"{SHARESANSAR_URL.format(slug=step.symbol.lower())} dividend and right-share tables: no action within "
                f"{HALT_GAP_SESSIONS} sessions; {MEROLAGANI_URL.format(symbol=step.symbol)} bonus and right-share sections: "
                f"{'none' if merolagani_status.get(step.symbol) == 'checked' else merolagani_status.get(step.symbol, 'not checked')}"
            )
            connection.execute(
                text(
                    "INSERT INTO price_quarantine (symbol, step_date, raw_move, reason, sources_checked) "
                    "VALUES (:s, :d, :m, :r, :c) ON CONFLICT (symbol, step_date) DO NOTHING"
                ),
                {"s": step.symbol, "d": step.date, "m": float(step.raw_move),
                 "r": f"{step.kind}: close {step.previous_close:g} to {step.close:g} with no corporate action found", "c": checked},
            )
            report["quarantined"].append({"symbol": step.symbol, "date": str(step.date), "raw_move": round(float(step.raw_move), 4), "kind": step.kind})
    report["merolagani"] = merolagani_status
    report["after"] = after[(after["date"] >= cutoff) & (after["kind"].isin(UNRESOLVED_KINDS) | (after["kind"] == RESOLVED))][
        ["symbol", "date", "base_move", "kind"]].astype(str).to_dict("records")
    return report


def live_check(lookback: int) -> int:
    from src.database.connection import engine as main_engine
    from src.database.holdout_guard import allow

    with main_engine.connect() as connection:
        latest = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one()
        quarantine = load_quarantine(connection)
    with allow("live_ledger"):
        inputs = load_inputs(latest)
    steps = detect_steps(inputs["prices"], inputs["actions"], inputs["sessions"])
    known = set(zip(quarantine["symbol"], quarantine["step_date"]))
    recent = check_recent(steps, inputs["sessions"], lookback, known)
    print(json.dumps({"latest_session": latest.isoformat(), "lookback": lookback, "unresolved_unquarantined": recent[:50],
                      "count": len(recent)}, indent=2, default=str))
    return 1 if recent else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", type=date.fromisoformat, default=PRE_HOLDOUT_END)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--recover-delisted", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--resolve-recent", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        sys.exit(live_check(args.lookback))
    if args.end > PRE_HOLDOUT_END:
        raise SystemExit(f"--end may not be inside the holdout (after {PRE_HOLDOUT_END})")
    inputs = load_inputs(args.end)
    if args.resolve_recent:
        from src.database.holdout_guard import engine

        print(json.dumps(resolve_recent(inputs, args.lookback, engine), indent=2, default=str))
        return
    steps = detect_steps(inputs["prices"], inputs["actions"], inputs["sessions"])
    if args.check:
        from src.database.holdout_guard import engine

        with engine.connect() as connection:
            quarantine = load_quarantine(connection)
        known = set(zip(quarantine["symbol"], quarantine["step_date"]))
        acknowledged = [
            r for r in recent_unresolved(steps, inputs["sessions"], args.lookback)[["symbol", "date", "raw_move", "kind"]].astype(str).to_dict("records")
            if (r["symbol"], date.fromisoformat(r["date"])) in known
        ]
        for row in acknowledged:
            print(f"quarantined (excluded from live calls): {row}")
        recent = check_recent(steps, inputs["sessions"], args.lookback, known)
        if recent:
            print(f"DATA QUALITY FAILURE: {len(recent)} unresolved price steps in the last {args.lookback} sessions")
            for row in recent[:50]:
                print(row)
            sys.exit(1)
        print(f"price steps check passed: no unquarantined unresolved steps in the last {args.lookback} sessions")
        return
    report: dict[str, Any] = {
        "generated_on": date.today().isoformat(),
        "end": args.end.isoformat(),
        "band": "10% before 2026-04-20, 15% from then (code constant); tolerance 0.5 point; halt = gap >= 20 sessions",
        "before_recovery": summarize(steps, inputs["companies"]),
        "calibration": calibrate(inputs["prices"], inputs["actions"], inputs["sessions"]),
    }
    if args.recover_delisted:
        status = dict(zip(inputs["companies"]["symbol"], inputs["companies"]["status"]))
        targets = sorted(
            {s for s, k in zip(steps["symbol"], steps["kind"]) if k in UNRESOLVED_KINDS and status.get(s) in ("D", "S")}
        )
        report["recovery"] = recover_delisted(targets)
        inputs = load_inputs(args.end)
        steps = detect_steps(inputs["prices"], inputs["actions"], inputs["sessions"])
        report["after_recovery"] = summarize(steps, inputs["companies"])
    steps.to_csv(args.report.with_suffix(".steps.csv"), index=False)
    args.report.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "recovery"}, indent=2, default=str))
    if "recovery" in report:
        print(json.dumps({k: v for k, v in report["recovery"].items() if k != "per_symbol"}, indent=2))


if __name__ == "__main__":
    main()
