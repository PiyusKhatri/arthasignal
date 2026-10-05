from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.league import bots as lb
from src.league.bots import RISK, League
from src.scorecard import model_v0, spec
from src.scorecard.daily import EXIT_NO_SESSION, NPT, build_state, entry_deadline, quarantined_symbols

logger = logging.getLogger(__name__)

THIN_SESSION_SHARE = 0.9
RECENT_SESSIONS = 10


def _stages(rows: np.ndarray, checks: list[tuple[str, Callable[[int], bool]]]) -> tuple[list[dict[str, Any]], np.ndarray]:
    out = [{"filter": "start", "passed": int(len(rows))}]
    for name, check in checks:
        kept = np.array([r for r in rows if check(int(r))], dtype=int)
        out.append({"filter": name, "passed": int(len(kept)), "dropped": int(len(rows) - len(kept))})
        rows = kept
    return out, rows


def _symbols(panel: es.Panel, rows: Any) -> list[str]:
    return sorted(panel.symbols[int(r)] for r in rows)


def _after_block(picks: pd.DataFrame, blocked: set[str]) -> dict[str, Any]:
    removed = sorted(set(picks["symbol"]) & blocked)
    return {"picks": int(len(picks)), "removed_by_quarantine_or_exclusion": removed,
            "written_would_be": int(len(picks) - len(removed))}


def recent_sessions(state: dict[str, Any], count: int = RECENT_SESSIONS) -> dict[str, Any]:
    panel = state["panel"]
    traded = ~np.isnan(panel.close)
    per_session = traded.sum(axis=0)
    window = per_session[-RISK["liquidity_lookback_sessions"]:]
    median = float(np.median(window)) if len(window) else 0.0
    index_days = set(state["inputs"]["index"]["date"])
    lo = max(0, len(panel.sessions) - RISK["liquidity_lookback_sessions"])
    thin = [panel.sessions[c].isoformat() for c in range(lo, len(panel.sessions)) if per_session[c] < THIN_SESSION_SHARE * median]
    rows = []
    for c in range(max(0, len(panel.sessions) - count), len(panel.sessions)):
        both = traded[:, c] & (traded[:, c - 1] if c else False)
        unchanged = int(np.sum(both & (panel.close[:, c] == panel.close[:, c - 1]))) if c else 0
        rows.append({"session": panel.sessions[c].isoformat(), "equities_traded": int(per_session[c]),
                     "equities_with_close_unchanged_from_previous_session": unchanged,
                     "price_steps_flagged": int(panel.corrupt[:, c].sum()), "nepse_index_row": panel.sessions[c] in index_days})
    missing_index = [panel.sessions[c].isoformat() for c in range(lo, len(panel.sessions)) if panel.sessions[c] not in index_days]
    return {"panel_sessions": len(panel.sessions), "panel_symbols": len(panel.symbols),
            "median_equities_traded_last_60": median, "thin_sessions_last_60": thin,
            "sessions_without_nepse_index_last_60": missing_index, "last_sessions": rows}


def market_state(league: League, panel: es.Panel, t: int) -> dict[str, Any]:
    context = league._context(panel)
    index = league.index[league.index["date"] <= panel.sessions[-1]]
    close = index.set_index("date")["close"].reindex(pd.Index(panel.sessions)).ffill()
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    value = lambda s: None if pd.isna(s.iloc[t]) else round(float(s.iloc[t]), 2)
    return {
        "state": context["states"][t],
        "nepse_close": value(close), "sma50": value(sma50), "sma200": value(sma200),
        "close_over_sma200": None if pd.isna(sma200.iloc[t]) else round(float(close.iloc[t] / sma200.iloc[t]), 4),
        "rule": "bear: close < SMA200 and SMA50 < SMA200; bull: close > SMA200 and SMA50 > SMA200 and close < 1.2 x SMA200; "
                "overheated: close >= 1.2 x SMA200; otherwise sideways",
        "last_sessions": {panel.sessions[c].isoformat(): context["states"][c] for c in range(max(0, t - RECENT_SESSIONS + 1), t + 1)},
    }


def eligibility(league: League, panel: es.Panel, t: int, thin: set[date]) -> dict[str, Any]:
    context = league._context(panel)
    lookback = RISK["liquidity_lookback_sessions"]
    lo = max(0, t - lookback + 1)
    traded_count = context["cum_traded"][:, t + 1] - context["cum_traded"][:, lo]
    corrupt = context["cum_corrupt"][:, t + 1] - context["cum_corrupt"][:, max(0, t - lb.RANKER_MOMENTUM_LOOKBACK + 1)] > 0
    reference, turnover = league.eligible(panel, t)
    thin_cols = [c for c in range(lo, t + 1) if panel.sessions[c] in thin]
    stages, rows = _stages(np.flatnonzero(context["traded"][:, t]), [
        ("valid symbol", lambda r: spec.valid_symbol(panel.symbols[r])),
        (f"traded on at least {RISK['min_traded_sessions_in_lookback']} of the last {lookback} sessions",
         lambda r: traded_count[r] >= RISK["min_traded_sessions_in_lookback"]),
        (f"{lookback}-session median turnover at least Rs {RISK['min_median_turnover_npr']:,}",
         lambda r: bool(turnover[r] >= RISK["min_median_turnover_npr"])),
        (f"no unresolved price step in the last {lb.RANKER_MOMENTUM_LOOKBACK} sessions", lambda r: not corrupt[r]),
        ("did not close at the upper limit on the signal day", lambda r: not league._upper_limit_close(panel, context, r, t)),
    ])
    short = [r for r in np.flatnonzero(context["traded"][:, t])
             if spec.valid_symbol(panel.symbols[r]) and traded_count[r] < RISK["min_traded_sessions_in_lookback"]]
    rescued = [r for r in short if traded_count[r] + sum(1 for c in thin_cols if np.isnan(panel.close[r, c])) >= RISK["min_traded_sessions_in_lookback"]]
    return {
        "stages": stages,
        "eligible": int(len(rows)),
        "matches_bot_code": sorted(map(int, rows)) == sorted(map(int, reference)),
        "short_history_symbols_that_only_miss_thin_sessions": _symbols(panel, rescued),
    }, rows, turnover


def momentum(league: League, panel: es.Panel, t: int, rows: np.ndarray, blocked: set[str]) -> dict[str, Any]:
    context = league._context(panel)
    state = context["states"][t]
    established = [r for r in rows if not league._new_listing(panel, context, r, t)]
    m_rows, m_scores = league.momentum_scores(panel, t)
    ungated = league._cap(panel, m_rows, m_scores, RISK["max_calls_per_day"])
    picks = league.select_momentum(panel, t)
    return {
        "conditions": {"state is not market_bear": state != "market_bear", f"at least {lb.MOMENTUM_LOOKBACK} sessions of history": t >= lb.MOMENTUM_LOOKBACK},
        "eligible": int(len(rows)), "eligible_not_new_listings": len(established),
        "picks_if_state_gate_were_open": ungated["symbol"].tolist(),
        **_after_block(picks, blocked),
    }


def new_listing(league: League, panel: es.Panel, t: int, blocked: set[str]) -> dict[str, Any]:
    context = league._context(panel)
    first = context["first"]
    state = context["states"][t]
    stages, rows = _stages(np.flatnonzero(context["traded"][:, t]), [
        ("valid symbol", lambda r: spec.valid_symbol(panel.symbols[r])),
        ("not a merger symbol", lambda r: panel.symbols[r] not in league.mergers),
        (f"first price at panel session {et.LISTING_MIN_PANEL_SESSIONS} or later", lambda r: first[r] >= et.LISTING_MIN_PANEL_SESSIONS),
        (f"first price after {et.LISTING_VISIBLE_AFTER}", lambda r: panel.sessions[first[r]] > et.LISTING_VISIBLE_AFTER),
        (f"first price within the last {model_v0.NEW_LISTING_SESSIONS} sessions", lambda r: t - first[r] < model_v0.NEW_LISTING_SESSIONS),
        ("did not close at the upper limit on the signal day", lambda r: not league._upper_limit_close(panel, context, r, t)),
    ])
    ungated = (league._cap(panel, rows, league._trailing(context, t - lb.MOMENTUM_LOOKBACK + 1, t)[rows], lb.NEW_LISTING_PICKS)
               if len(rows) and t >= lb.MOMENTUM_LOOKBACK else lb.EMPTY.copy())
    picks = league.select_new_listing(panel, t)
    return {
        "conditions": {"state is not market_bear": state != "market_bear", f"at least {lb.MOMENTUM_LOOKBACK} sessions of history": t >= lb.MOMENTUM_LOOKBACK},
        "stages": stages, "new_listings": _symbols(panel, rows),
        "picks_if_state_gate_were_open": ungated["symbol"].tolist(),
        **_after_block(picks, blocked),
    }


def ranker(league: League, panel: es.Panel, t: int, rows: np.ndarray, blocked: set[str]) -> dict[str, Any]:
    picks = league.select_ranker(panel, t)
    return {"conditions": {f"at least {lb.RANKER_MOMENTUM_LOOKBACK} sessions of history": t >= lb.RANKER_MOMENTUM_LOOKBACK},
            "eligible": int(len(rows)), "symbols": picks["symbol"].tolist(), **_after_block(picks, blocked)}


def timer(league: League, panel: es.Panel, t: int, rows: np.ndarray, turnover: np.ndarray, blocked: set[str]) -> dict[str, Any]:
    context = league._context(panel)
    state = context["states"][t]
    history = t >= max(lb.TIMER_BREADTH_SMA, lb.TIMER_INDEX_LOOKBACK)
    breadth = above = members_count = None
    index_return = None
    if history:
        closes = pd.DataFrame(panel.close[:, t - lb.TIMER_BREADTH_SMA + 1 : t + 1].T).ffill().to_numpy().T
        members = np.flatnonzero(context["traded"][:, t])
        members_count = int(len(members))
        if members_count:
            with np.errstate(all="ignore"):
                sma = np.nanmean(closes[members], axis=1)
                flags = panel.close[members, t] > sma
            above = int(flags.sum())
            breadth = round(float(np.mean(flags)), 4)
        level = context["index_close"]
        start = level[t - lb.TIMER_INDEX_LOOKBACK]
        if np.isfinite(start) and start > 0:
            index_return = round(float(level[t] / start - 1), 4)
    conditions = {
        f"state in {list(lb.TIMER_ON_STATES)}": state in lb.TIMER_ON_STATES,
        f"at least {max(lb.TIMER_BREADTH_SMA, lb.TIMER_INDEX_LOOKBACK)} sessions of history": history,
        f"breadth (share above own {lb.TIMER_BREADTH_SMA}-session average) >= {lb.TIMER_BREADTH_MIN}": breadth is not None and breadth >= lb.TIMER_BREADTH_MIN,
        f"NEPSE {lb.TIMER_INDEX_LOOKBACK}-session return > 0": index_return is not None and index_return > 0,
    }
    on = league.timer_on(panel, t)
    picks = league.select_timer(panel, t)
    return {"conditions": conditions, "timer_on": on, "matches_bot_code": on == all(conditions.values()),
            "breadth": breadth, "equities_above_own_average": above, "equities_traded": members_count,
            "nepse_20_session_return": index_return, "eligible": int(len(rows)),
            "basket_if_timer_were_on": league._cap(panel, rows, turnover[rows] / 1e6, RISK["max_calls_per_day"])["symbol"].tolist(),
            **_after_block(picks, blocked)}


def combined(league: League, panel: es.Panel, t: int, blocked: set[str]) -> dict[str, Any]:
    m_rows, m_scores = league.momentum_scores(panel, t)
    r_rows, r_scores = league.ranker_scores(panel, t)
    top_m = {int(m_rows[i]) for i in np.argsort(-m_scores, kind="stable")[: lb.COMBINED_CANDIDATES]} if len(m_rows) else set()
    top_r = {int(r_rows[i]) for i in np.argsort(-r_scores, kind="stable")[: lb.COMBINED_CANDIDATES]} if len(r_rows) else set()
    in_both = {r for r in top_m | top_r if r in set(map(int, m_rows)) and r in set(map(int, r_rows))}
    avoided = league.avoid_symbols(panel, t)
    candidates = {r for r in in_both if panel.symbols[r] not in avoided}
    picks = league.select_combined(panel, t)
    return {"conditions": {"market timer on": league.timer_on(panel, t)},
            "momentum_scored": int(len(m_rows)), "ranker_scored": int(len(r_rows)),
            "top_candidates_union": len(top_m | top_r), "scored_by_both": len(in_both),
            "after_removing_avoid_hits": len(candidates), **_after_block(picks, blocked)}


def avoid(league: League, state: dict[str, Any], t: int, blocked: set[str]) -> dict[str, Any]:
    panel = state["panel"]
    v0 = league.v0
    context = v0._context(panel)
    start, end = panel.sessions[max(0, t - model_v0.AVOID_AFTER_BONUS_SESSIONS + 1)], panel.sessions[t]
    traded = set(np.flatnonzero(~np.isnan(panel.close[:, t])))
    actions = state["actions"]
    bonus = actions[actions["action_type"].astype(str).str.upper() == "BONUS"]
    in_window = bonus[(bonus["action_date"] >= start) & (bonus["action_date"] <= end)]
    e2 = sorted({s for s in in_window["symbol"] if s in panel.row and panel.row[s] in traded and spec.valid_symbol(s)})
    lo = max(0, t - model_v0.AVOID_AFTER_STREAK_SESSIONS)
    ends = context["streak_end"][:, t] - (context["streak_end"][:, lo] if lo > 0 else 0)
    e4 = sorted(panel.symbols[r] for r in traded if ends[r] > 0 and spec.valid_symbol(panel.symbols[r]))
    up_close, _, _, _ = et.circuit_flags(panel)
    window = slice(max(0, t - model_v0.AVOID_AFTER_STREAK_SESSIONS + 1), t + 1)
    ongoing = []
    for r in range(len(panel.symbols)):
        streak = 0
        for c in np.flatnonzero(~np.isnan(panel.close[r, : t + 1])):
            streak = streak + 1 if up_close[r, c] else 0
        if streak >= model_v0.STREAK_MIN:
            ongoing.append(panel.symbols[r])
    by_month = actions.assign(month=pd.to_datetime(actions["action_date"]).dt.strftime("%Y-%m"))
    months = sorted(by_month["month"].unique())[-12:]
    coverage = {m: by_month[by_month["month"] == m]["action_type"].astype(str).str.upper().value_counts().to_dict() for m in months}
    picks = league.select_avoid(panel, t)
    hits = v0.avoid_hits(panel, t)
    return {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "e2_bonus_book_close": {"bonus_rows_loaded": int(len(bonus)),
                                "latest_bonus_date_loaded": None if bonus.empty else str(max(bonus["action_date"])),
                                "bonus_rows_in_window": int(len(in_window)), "hits": e2},
        "e4_upper_streak_end": {"upper_limit_closes_in_window": int(up_close[:, window].sum()),
                                "symbols_with_upper_limit_close_in_window": int(up_close[:, window].any(axis=1).sum()),
                                "hits": e4, "streaks_of_3_or_more_still_running_not_counted_until_they_end": sorted(ongoing)},
        "corporate_actions_loaded_by_month": coverage,
        "matches_bot_code": sorted(set(e2) | set(e4)) == sorted(set(hits["symbol"])),
        "model_v0_1_avoid_observations": int(len(hits[~hits["symbol"].isin(blocked)])),
        **_after_block(picks, blocked),
    }


def diagnose(state: dict[str, Any], blocked: set[str]) -> dict[str, Any]:
    panel = state["panel"]
    t = len(panel.sessions) - 1
    league = League(state["inputs"]["index"], state["actions"], state["mergers"])
    sessions = recent_sessions(state)
    thin = {date.fromisoformat(d) for d in sessions["thin_sessions_last_60"]}
    eligible, rows, turnover = eligibility(league, panel, t, thin)
    return {
        "signal_date": panel.sessions[t].isoformat(),
        "data": sessions,
        "market_state": market_state(league, panel, t),
        "eligibility": eligible,
        "bots": {
            "bot_avoid_e2e4": avoid(league, state, t, blocked),
            "bot_momentum": momentum(league, panel, t, rows, blocked),
            "bot_new_listing": new_listing(league, panel, t, blocked),
            "bot_ranker_spec": ranker(league, panel, t, rows, blocked),
            "bot_market_timer": timer(league, panel, t, rows, turnover, blocked),
            "bot_combined": combined(league, panel, t, blocked),
        },
    }


def ledger(engine: Any, as_of: date) -> dict[str, Any]:
    with engine.connect() as connection:
        written = connection.execute(
            text("SELECT strategy, model_version, count(*) FROM scorecard_calls WHERE mode = 'live' AND signal_date = :d "
                 "GROUP BY strategy, model_version ORDER BY strategy"), {"d": as_of}).all()
        runs = connection.execute(
            text("SELECT started_at, report FROM league_runs WHERE as_of = :d ORDER BY id"), {"d": as_of}).all()
        companies = connection.execute(
            text("SELECT count(*) FILTER (WHERE instrument_type = 'Equity'), "
                 "count(*) FILTER (WHERE instrument_type = 'Equity' AND status = 'A') FROM companies")).one()
        rows = connection.execute(
            text("SELECT p.date, count(*), count(*) FILTER (WHERE c.instrument_type = 'Equity'), "
                 "count(*) FILTER (WHERE c.instrument_type = 'Equity' AND c.status = 'A') "
                 "FROM daily_prices p JOIN companies c ON c.symbol = p.symbol "
                 "WHERE p.date IN (SELECT DISTINCT date FROM daily_prices WHERE date <= :d ORDER BY date DESC LIMIT :n) "
                 "GROUP BY p.date ORDER BY p.date"), {"d": as_of, "n": RECENT_SESSIONS}).all()
        actions = connection.execute(
            text("SELECT action_type::text, count(*), max(action_date) FROM corporate_actions WHERE action_date > :d GROUP BY 1"),
            {"d": as_of}).all()
    return {
        "live_calls_in_ledger_for_signal_date": {f"{s} {v}": int(n) for s, v, n in written},
        "league_runs_for_signal_date": [
            {"started_at": str(started), "now": report.get("now"), "deadline": report.get("deadline"),
             "deadline_passed": report.get("deadline_passed"), "write": report.get("write"),
             "bots": {name: {k: entry.get(k) for k in ("calls", "suspended", "write")} for name, entry in (report.get("bots") or {}).items()},
             "excluded_today": report.get("excluded_today")}
            for started, report in runs
        ],
        "companies": {"equity": int(companies[0]), "equity_active": int(companies[1])},
        "daily_prices_rows": [{"date": str(d), "all": int(a), "equity": int(e), "equity_active": int(x)} for d, a, e, x in rows],
        "corporate_actions_dated_after_signal_date": {k: {"rows": int(n), "latest": str(m)} for k, n, m in actions},
    }


def verdicts(report: dict[str, Any]) -> dict[str, str]:
    bots = report["bots"]
    state = report["market_state"]["state"]
    out = {}
    for name, entry in bots.items():
        failed = [k for k, v in entry.get("conditions", {}).items() if not v]
        if entry["written_would_be"]:
            out[name] = f"{entry['written_would_be']} calls"
        elif failed:
            out[name] = "0: failed " + "; ".join(failed)
        elif entry["picks"]:
            out[name] = "0: every pick was quarantined or excluded"
        else:
            out[name] = "0: no symbol passed the filters"
    avoid = bots["bot_avoid_e2e4"]
    if not avoid["picks"] and not avoid["e2_bonus_book_close"]["bonus_rows_in_window"]:
        out["bot_avoid_e2e4"] += (f"; E2 had no bonus book close in the window and the latest bonus date loaded is "
                                  f"{avoid['e2_bonus_book_close']['latest_bonus_date_loaded']}")
    out["market_state"] = state
    return out


def run(as_of: date | None, exclusions: dict[str, str] | None = None) -> dict[str, Any]:
    from src.database.connection import engine as main_engine
    from src.database.holdout_guard import HOLDOUT_START, HoldoutQueryViolation, allow
    from src.database.holdout_guard import engine as research
    from src.ops.exclusions import excluded_symbols

    if as_of is None or as_of >= HOLDOUT_START:
        with main_engine.connect() as connection:
            latest = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one()
        as_of = as_of or latest
        if as_of >= HOLDOUT_START and as_of != latest:
            raise HoldoutQueryViolation(f"{as_of} is inside the holdout and is not the latest session ({latest}); only live operation may read it")
    past = as_of < HOLDOUT_START
    exclusions = excluded_symbols() if exclusions is None else exclusions

    def _go() -> dict[str, Any]:
        engine = research if past else main_engine
        state = build_state(as_of)
        quarantine = quarantined_symbols(engine, until=as_of if past else None)
        blocked = set(quarantine) | set(exclusions)
        report = diagnose(state, blocked)
        now = datetime.now(tz=NPT)
        report["role"] = "research (development date)" if past else "app role under the live_ledger exception"
        report["deadline"] = {"next_session_open": entry_deadline(as_of).isoformat(), "now": now.isoformat(),
                              "passed": now >= entry_deadline(as_of)}
        report["quarantined"] = len(quarantine)
        report["excluded_today"] = sorted(exclusions)
        report["ledger"] = ledger(engine, as_of)
        report["verdict"] = verdicts(report)
        return report

    if past:
        return _go()
    with allow("live_ledger"):
        return _go()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Read-only: explain each league bot's calls for a session filter by filter")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--exclusions", help="exclude_symbols.json written by the integrity step (default: $ARTHASIGNAL_EXCLUSIONS)")
    args = parser.parse_args()
    exclusions = None
    if args.exclusions:
        path = Path(args.exclusions)
        data = json.loads(path.read_text()) if path.exists() else {}
        exclusions = {str(k): str(v) for k, v in (data.get("symbols") or {}).items()}
    try:
        report = run(args.date, exclusions)
    except LookupError as error:
        logger.error("%s", error)
        sys.exit(EXIT_NO_SESSION)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
