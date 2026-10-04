from __future__ import annotations

import argparse
import bisect
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.scorecard import info_spec, spec, v2
from src.scorecard.ledger import feature_hash, graded_call_ids, load_graded, record_calls, record_grades
from src.scorecard.replay_v2 import build_context
from src.scorecard.situations import labels_for

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("docs/info_results.json")
MODEL_VERSION = "v1"
AUDIT_DATES = 40
REPORT_EVENTS = ("I1", "I2", "I3", "I6")
DECLARATION_EVENTS = ("I4", "I5")


def prior_fiscal_year(fiscal_year: str) -> str | None:
    try:
        start = int(fiscal_year.split("/")[0])
    except (ValueError, AttributeError):
        return None
    return f"{start - 1}/{start}"


def load_reports(engine: Any, end: date) -> pd.DataFrame:
    with engine.connect() as connection:
        ss = pd.read_sql(
            text(
                "SELECT symbol, fiscal_year, quarter, net_profit::float AS net_profit, published_date FROM quarterly_report_announcements "
                "WHERE source = 'sharesansar' AND NOT is_correction AND net_profit IS NOT NULL AND symbol IS NOT NULL "
                "AND fiscal_year IS NOT NULL AND quarter IS NOT NULL AND published_date <= :e"
            ),
            connection,
            params={"e": end},
        )
        ml = pd.read_sql(
            text(
                "SELECT resolved_symbol AS symbol, fiscal_year, quarter, min(published_date) AS ml_date "
                "FROM quarterly_report_announcements_resolved WHERE source = 'merolagani' AND NOT is_correction "
                "AND resolved_symbol IS NOT NULL AND fiscal_year IS NOT NULL AND quarter IS NOT NULL AND published_date <= :e "
                "GROUP BY 1, 2, 3"
            ),
            connection,
            params={"e": end},
        )
    keys = ["symbol", "fiscal_year", "quarter"]
    ss = ss.sort_values("published_date").drop_duplicates(keys, keep="first")
    merged = ss.merge(ml, on=keys, how="left")
    merged["available_date"] = [max(a, b) if pd.notna(b) else a for a, b in zip(merged["published_date"], merged["ml_date"])]
    return merged.reset_index(drop=True)


def load_declarations(engine: Any, end: date) -> pd.DataFrame:
    with engine.connect() as connection:
        frame = pd.read_sql(
            text(
                "SELECT symbol, fiscal_year, cash_dividend_pct::float AS cash, bonus_pct::float AS bonus, announcement_date, bookclose_date "
                "FROM dividend_declarations WHERE announcement_date <= :e ORDER BY symbol, announcement_date"
            ),
            connection,
            params={"e": end},
        )
    frame["cash"] = frame["cash"].fillna(0.0)
    frame["bonus"] = frame["bonus"].fillna(0.0)
    frame["total"] = frame["cash"] + frame["bonus"]
    frame["available_date"] = frame["announcement_date"]
    return frame


def knowledge_index(sessions: Sequence[date], available: Sequence[date]) -> np.ndarray:
    return np.array([bisect.bisect_right(sessions, d) for d in available], dtype=int)


def add_growth(reports: pd.DataFrame, sessions: Sequence[date]) -> pd.DataFrame:
    frame = reports.copy()
    frame["t"] = knowledge_index(sessions, frame["available_date"])
    lookup = {(s, fy, q): (np_, t) for s, fy, q, np_, t in zip(frame["symbol"], frame["fiscal_year"], frame["quarter"], frame["net_profit"], frame["t"])}
    prior_profit, growth = [], []
    for s, fy, q, np_, t in zip(frame["symbol"], frame["fiscal_year"], frame["quarter"], frame["net_profit"], frame["t"]):
        prior = lookup.get((s, prior_fiscal_year(fy), q))
        if prior is None or prior[1] >= t:
            prior_profit.append(np.nan)
            growth.append(np.nan)
            continue
        prior_profit.append(prior[0])
        growth.append((np_ - prior[0]) / abs(prior[0]) if abs(prior[0]) >= info_spec.MIN_PRIOR_PROFIT_ABS else np.nan)
    frame["prior_profit"] = prior_profit
    frame["growth"] = growth
    return frame


def quartile_thresholds(reports: pd.DataFrame, n_sessions: int) -> dict[int, float]:
    valid = reports.dropna(subset=["growth"])
    order = np.argsort(valid["t"].to_numpy())
    ts = valid["t"].to_numpy()[order]
    values = valid["growth"].to_numpy()[order]
    out = {}
    for t in sorted(set(int(x) for x in reports["t"])):
        lo = bisect.bisect_left(ts, t - info_spec.QUARTILE_LOOKBACK_SESSIONS)
        hi = bisect.bisect_left(ts, t)
        window = values[lo:hi]
        if len(window) >= info_spec.QUARTILE_MIN_REPORTS:
            out[t] = float(np.quantile(window, 0.75))
    return out


def select_events(
    reports: pd.DataFrame,
    declarations: pd.DataFrame,
    sessions: Sequence[date],
    symbols: set[str],
    sectors: dict[str, str | None],
) -> dict[str, pd.DataFrame]:
    n = len(sessions)
    rep = add_growth(reports, sessions)
    rep = rep[(rep["t"] < n) & rep["symbol"].isin(symbols)]
    thresholds = quartile_thresholds(rep, n)
    rep = rep.assign(threshold=rep["t"].map(thresholds))
    top = rep[rep["growth"].notna() & rep["threshold"].notna() & (rep["growth"] >= rep["threshold"])]
    out: dict[str, pd.DataFrame] = {
        "I1": top,
        "I2": top,
        "I3": rep[(rep["net_profit"] > 0) & (rep["prior_profit"] < 0)],
        "I6": top[top["symbol"].map(sectors) == info_spec.BANK_SECTOR],
        "all_reports_20": rep[rep["growth"].notna()],
    }
    dec = declarations.copy()
    dec["t"] = knowledge_index(sessions, dec["available_date"])
    dec = dec[(dec["t"] < n) & dec["symbol"].isin(symbols)].sort_values(["symbol", "announcement_date"])
    dec["previous_total"] = dec.groupby("symbol")["total"].shift(1)
    dec["book_close_index"] = [bisect.bisect_left(sessions, d) if pd.notna(d) else -1 for d in dec["bookclose_date"]]
    out["I4"] = dec[(dec["total"] > 0) & dec["previous_total"].notna() & (dec["total"] >= dec["previous_total"])]
    out["I5"] = dec[(dec["bonus"] > 0) & (dec["book_close_index"] >= dec["t"] + info_spec.BOOK_CLOSE_MIN_GAP_SESSIONS)]
    out["all_declarations_10"] = dec
    return {k: v.drop_duplicates(["symbol", "t"]).reset_index(drop=True) for k, v in out.items()}


def lookahead_audit(reports: pd.DataFrame, declarations: pd.DataFrame, sessions: Sequence[date], symbols: set[str],
                    sectors: dict[str, str | None], full: dict[str, pd.DataFrame]) -> dict[str, Any]:
    candidates = sorted({int(t) for frame in full.values() for t in frame["t"]})
    rng = np.random.default_rng(20261004)
    sample = sorted(rng.choice(candidates, size=min(AUDIT_DATES, len(candidates)), replace=False).tolist()) if candidates else []
    mismatches = {}
    for t in sample:
        cut = sessions[t]
        truncated = select_events(
            reports[reports["available_date"] < cut], declarations[declarations["available_date"] < cut], sessions[: t + 1], symbols, sectors
        )
        for key, frame in full.items():
            a = set(frame.loc[frame["t"] == t, "symbol"])
            b = set(truncated[key].loc[truncated[key]["t"] == t, "symbol"])
            if a != b:
                mismatches.setdefault(key, []).append(str(cut))
    return {"dates_checked": len(sample), "mismatches": {k: len(v) for k, v in mismatches.items()}, "leaky": sorted(mismatches)}


def to_calls(events: pd.DataFrame, name: str, sessions: Sequence[date], panel: Any, situations: Any, batch: str,
             model_version: str = MODEL_VERSION) -> pd.DataFrame:
    frame = pd.DataFrame({"symbol": events["symbol"].to_numpy(), "t": events["t"].to_numpy(dtype=int)})
    frame["signal_date"] = [sessions[t] for t in frame["t"]]
    frame["score"] = events["growth"].to_numpy(dtype=float) if "growth" in events else np.nan
    frame["situations"] = [labels_for(situations, int(panel.row[s]), int(t)) for s, t in zip(frame["symbol"], frame["t"])]
    frame["mode"] = "replay"
    frame["strategy"] = name
    frame["model_version"] = model_version
    frame["batch_id"] = batch
    frame["probability"] = None
    frame["feature_hash"] = [feature_hash({"strategy": name, "symbol": s, "date": d}) for s, d in zip(frame["symbol"], frame["signal_date"])]
    return frame.drop(columns=["t"])


def per_year(frame: pd.DataFrame, column: str = "signal_date") -> dict[int, int]:
    return {int(y): int(n) for y, n in pd.to_datetime(frame[column]).dt.year.value_counts().sort_index().items()}


def run(output: Path = DEFAULT_OUTPUT, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    from src.database.holdout_guard import engine

    started = time.perf_counter()
    context = build_context()
    panel, market, cubes = context["panel"], context["market"], context["cubes"]
    sessions = list(panel.sessions)
    if sessions[-1] > info_spec.DEVELOPMENT_END:
        raise SystemExit("panel extends past the development window")
    session_index = {d: i for i, d in enumerate(sessions)}
    symbols = set(panel.symbols)
    sectors = dict(zip(context["inputs"]["companies"]["symbol"], context["inputs"]["companies"]["sector"]))
    reports = load_reports(engine, info_spec.DEVELOPMENT_END)
    declarations = load_declarations(engine, info_spec.DEVELOPMENT_END)
    events = select_events(reports, declarations, sessions, symbols, sectors)
    audit = lookahead_audit(reports, declarations, sessions, symbols, sectors, events)
    with engine.connect() as connection:
        versions = int(connection.execute(text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = :f"), {"f": info_spec.FAMILY}).scalar_one())
    tests = versions * len(spec.SITUATIONS) * len(spec.HORIZONS)
    report: dict[str, Any] = {
        "protocol": info_spec.PROTOCOL_VERSION,
        "gate_protocol": v2.PROTOCOL_VERSION,
        "grade_version": info_spec.GRADE_VERSION,
        "generated_on": date.today().isoformat(),
        "sessions": len(sessions),
        "first_session": sessions[0].isoformat(),
        "last_session": sessions[-1].isoformat(),
        "versions_in_family": versions,
        "penalty_tests": tests,
        "inputs": {
            "reports_with_net_profit": int(len(reports)),
            "reports_with_merolagani_date": int(reports["ml_date"].notna().sum()),
            "reports_later_date_from_merolagani": int((reports["ml_date"].notna() & (reports["ml_date"] > reports["published_date"])).sum()),
            "reports_with_growth": int(len(events["all_reports_20"])),
            "declarations": int(len(declarations)),
            "first_declaration": str(declarations["announcement_date"].min()),
        },
        "audit": audit,
        "hypotheses": {},
        "diagnostics": {},
    }
    batch = f"info-{date.today().isoformat()}"
    for hypothesis_id, hypothesis in info_spec.HYPOTHESES.items():
        horizon = hypothesis["horizon"]
        name = info_spec.strategy_name(hypothesis_id)
        frame = events[hypothesis_id]
        with engine.connect() as connection:
            existing = pd.read_sql(
                text("SELECT id AS call_id, symbol, signal_date FROM scorecard_calls WHERE strategy = :s AND model_version = :v"),
                connection, params={"s": name, "v": model_version},
            )
        if existing.empty and len(frame):
            record_calls(engine, to_calls(frame, name, sessions, panel, context["situations"], batch, model_version))
            with engine.connect() as connection:
                existing = pd.read_sql(
                    text("SELECT id AS call_id, symbol, signal_date FROM scorecard_calls WHERE strategy = :s AND model_version = :v"),
                    connection, params={"s": name, "v": model_version},
                )
        done = graded_call_ids(engine, name, model_version, grade_version=info_spec.GRADE_VERSION)
        if not existing.empty:
            grades = v2.grade_calls_v2(market, {horizon: cubes[horizon]}, existing, context["cumulative"], info_spec.GRADE_VERSION)
            if done and not grades.empty:
                grades = grades[[(c, h) not in done for c, h in zip(grades["call_id"], grades["horizon"])]]
            if not grades.empty:
                record_grades(engine, grades)
        graded = load_graded(engine, name, model_version, horizon=horizon, grade_version=info_spec.GRADE_VERSION)
        if (graded["exit_date"].dropna() > info_spec.DEVELOPMENT_END).any():
            raise SystemExit("a graded exit reached past the development window")
        cell = v2.cell_metrics_v2(graded, horizon, session_index, len(sessions), tests, hypothesis_id in audit["leaky"])
        report["hypotheses"][hypothesis_id] = {
            "name": hypothesis["name"],
            "horizon": horizon,
            "events": int(len(frame)),
            "events_per_year": per_year(pd.DataFrame({"signal_date": [sessions[t] for t in frame["t"]]})) if len(frame) else {},
            "graded_per_year": per_year(graded[graded["status"] != spec.STATUS_DATA_ERROR]) if len(graded) else {},
            **cell,
        }
        logger.info("%s: %s", hypothesis_id, cell.get("verdict"))
    for key, horizon in (("all_reports_20", 20), ("all_declarations_10", 10)):
        frame = events[key]
        calls = pd.DataFrame({"call_id": np.arange(len(frame)), "symbol": frame["symbol"].to_numpy(),
                              "signal_date": [sessions[t] for t in frame["t"]]})
        grades = v2.grade_calls_v2(market, {horizon: cubes[horizon]}, calls, context["cumulative"], info_spec.GRADE_VERSION)
        graded = grades.merge(calls, on="call_id")
        graded["probability"] = np.nan
        report["diagnostics"][key] = {"horizon": horizon, "events": int(len(frame)), "events_per_year": per_year(calls),
                                      **v2.cell_metrics_v2(graded, horizon, session_index, len(sessions), tests)}
    report["runtime_seconds"] = round(time.perf_counter() - started, 1)
    output.write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run the pre-registered earnings-information hypotheses on the development window")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-version", default=MODEL_VERSION)
    args = parser.parse_args()
    report = run(args.output, args.model_version)
    for hypothesis_id, cell in report["hypotheses"].items():
        print(hypothesis_id, cell["name"], cell.get("verdict"), "edge", cell.get("edge"), "calls", cell.get("calls"))


if __name__ == "__main__":
    main()
