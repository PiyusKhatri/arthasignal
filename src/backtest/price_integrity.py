from __future__ import annotations

import argparse
import bisect
import json
import logging
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
            day = sessions[c]
            raw_step = is_step(raw, day)
            adjusted_step = is_step(adjusted, day)
            open_step = False
            if day >= es.FIRST_REAL_OPEN_SESSION and opens[k] > 0:
                open_adjusted = es._apply_actions(opens[k], entries) / closes[k - 1] - 1.0
                open_step = is_step(open_adjusted, day)
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
                    "open_step": bool(open_step),
                    "actions_in_gap": ";".join(f"{kind_}:{value:g}" for kind_, value in entries),
                    "kind": kind,
                }
            )
    return pd.DataFrame.from_records(
        records,
        columns=[
            "symbol", "date", "session_index", "previous_date", "gap_sessions", "previous_close", "close",
            "raw_move", "adjusted_move", "open_step", "actions_in_gap", "kind",
        ],
    )


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
    from src.database.connection import engine

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


def check_recent(steps: pd.DataFrame, sessions: Sequence[date], lookback: int) -> list[dict[str, Any]]:
    if not sessions:
        return []
    cutoff = sessions[max(0, len(sessions) - lookback)]
    recent = steps[(steps["kind"].isin(UNRESOLVED_KINDS)) & (steps["date"] >= cutoff)]
    return recent[["symbol", "date", "raw_move", "adjusted_move", "kind"]].astype(str).to_dict("records")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", type=date.fromisoformat, default=PRE_HOLDOUT_END)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--recover-delisted", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lookback", type=int, default=60)
    args = parser.parse_args()
    if args.end > PRE_HOLDOUT_END:
        raise SystemExit(f"--end may not be inside the holdout (after {PRE_HOLDOUT_END})")
    inputs = load_inputs(args.end)
    steps = detect_steps(inputs["prices"], inputs["actions"], inputs["sessions"])
    if args.check:
        recent = check_recent(steps, inputs["sessions"], args.lookback)
        if recent:
            print(f"DATA QUALITY FAILURE: {len(recent)} unresolved price steps in the last {args.lookback} sessions")
            for row in recent[:50]:
                print(row)
            sys.exit(1)
        print(f"price steps check passed: no unresolved steps in the last {args.lookback} sessions")
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
