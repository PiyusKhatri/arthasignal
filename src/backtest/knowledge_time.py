from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "docs" / "point_in_time_reliability.json"
SLUG_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})$")
ML_WINDOW = 100
ML_MIN_PERIODS = 20
BACKDATE_DAYS = 7
QUARTER_END_TOLERANCE = 3
RELIABLE_BACKDATE_SHARE = 0.05
BS_AD_OFFSET = 57
QUARTER_END = {1: (0, 10, 16), 2: (1, 1, 13), 3: (1, 4, 12), 4: (1, 7, 15)}
KEYS = ["symbol", "fiscal_year", "quarter"]


def quarter_end(fiscal_year: Any, quarter: Any) -> date | None:
    try:
        start = int(str(fiscal_year).split("/")[0]) - BS_AD_OFFSET
        offset, month, day = QUARTER_END[int(quarter)]
    except (ValueError, TypeError, KeyError):
        return None
    return date(start + offset, month, day)


def _as_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NaT:
        return None
    return pd.Timestamp(value).date()


def sharesansar_dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    slug = out["source_id"].astype(str).str.extract(SLUG_DATE)[0]
    out["slug_date"] = [date.fromisoformat(s) if isinstance(s, str) else None for s in slug]
    out["quarter_end"] = [quarter_end(f, q) for f, q in zip(out["fiscal_year"], out["quarter"])]
    out["backdated_days"] = [(s - p).days if s is not None else None for s, p in zip(out["slug_date"], out["published_date"])]
    out["before_quarter_end"] = [q is not None and (q - p).days > QUARTER_END_TOLERANCE for q, p in zip(out["quarter_end"], out["published_date"])]
    out["knowledge_date"] = [max(p, s) if s is not None else p for p, s in zip(out["published_date"], out["slug_date"])]
    out["verified"] = ~out["before_quarter_end"]
    return out


def merolagani_dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["numeric_id"] = out["source_id"].astype(int)
    out = out.sort_values("numeric_id").reset_index(drop=True)
    stamps = pd.to_datetime(out["published_date"]).astype("int64")
    median = stamps.rolling(ML_WINDOW, min_periods=ML_MIN_PERIODS).median().shift(1)
    out["previous_ids_median"] = [pd.Timestamp(int(m)).date() if pd.notna(m) else None for m in median]
    out["quarter_end"] = [quarter_end(f, q) for f, q in zip(out["fiscal_year"], out["quarter"])]
    out["backdated_days"] = [(m - p).days if m is not None else None for m, p in zip(out["previous_ids_median"], out["published_date"])]
    out["before_quarter_end"] = [q is not None and (q - p).days > QUARTER_END_TOLERANCE for q, p in zip(out["quarter_end"], out["published_date"])]
    out["id_backdated"] = [b is not None and b > BACKDATE_DAYS for b in out["backdated_days"]]
    out["knowledge_date"] = [max(p, m) if m is not None else p for p, m in zip(out["published_date"], out["previous_ids_median"])]
    out["verified"] = ~out["before_quarter_end"]
    return out


def combine(ss: pd.DataFrame, ml: pd.DataFrame) -> pd.DataFrame:
    s = ss[~ss["is_correction"] & ss["symbol"].notna() & ss["fiscal_year"].notna() & ss["quarter"].notna()]
    s = s.sort_values("knowledge_date").drop_duplicates(KEYS, keep="first")
    m = ml[~ml["is_correction"] & ml["symbol"].notna() & ml["fiscal_year"].notna() & ml["quarter"].notna() & ml["verified"]]
    m = m.groupby(KEYS, as_index=False).agg(ml_date=("knowledge_date", "min"))
    merged = s.merge(m, on=KEYS, how="left")
    merged["ss_date"] = [k if v else None for k, v in zip(merged["knowledge_date"], merged["verified"])]
    merged["ml_date"] = [_as_date(d) for d in merged["ml_date"]]
    merged["available_date"] = [earliest(a, b) for a, b in zip(merged["ss_date"], merged["ml_date"])]
    return merged[merged["available_date"].notna()].drop(columns=["knowledge_date"]).reset_index(drop=True)


def earliest(*days: date | None) -> date | None:
    present = [d for d in days if d is not None and not pd.isna(d)]
    return min(present) if present else None


def load_sources(engine: Any, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    with engine.connect() as connection:
        frame = pd.read_sql(
            text(
                "SELECT q.source, q.source_id, coalesce(q.symbol, r.resolved_symbol) AS symbol, q.fiscal_year, q.quarter, q.is_correction, "
                "q.net_profit::float AS net_profit, q.published_date FROM quarterly_report_announcements q "
                "LEFT JOIN quarterly_report_announcements_resolved r ON r.id = q.id WHERE q.published_date <= :e"
            ),
            connection,
            params={"e": end},
        )
    frame["published_date"] = [_as_date(d) for d in frame["published_date"]]
    ss = sharesansar_dates(frame[frame["source"] == "sharesansar"])
    ml = merolagani_dates(frame[frame["source"] == "merolagani"])
    return ss, ml


def load_reports(engine: Any, end: date) -> pd.DataFrame:
    ss, ml = load_sources(engine, end)
    reports = combine(ss[ss["net_profit"].notna()], ml)
    return reports[["symbol", "fiscal_year", "quarter", "net_profit", "published_date", "ss_date", "ml_date", "available_date"]]


def truncate_reports(reports: pd.DataFrame, day: date) -> pd.DataFrame:
    mask = np.array([d is not None and not pd.isna(d) and d <= day for d in reports["available_date"]], dtype=bool)
    kept = reports.loc[mask].copy()
    kept["ss_date"] = [d if d is not None and not pd.isna(d) and d <= day else None for d in kept["ss_date"]]
    kept["ml_date"] = [d if d is not None and not pd.isna(d) and d <= day else None for d in kept["ml_date"]]
    kept["available_date"] = [earliest(a, b) for a, b in zip(kept["ss_date"], kept["ml_date"])]
    return kept


def _share(values: pd.Series) -> float | None:
    return float(values.mean()) if len(values) else None


def reliability(ss: pd.DataFrame, ml: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {"rules": {"ml_window": ML_WINDOW, "backdate_days": BACKDATE_DAYS, "quarter_end_tolerance": QUARTER_END_TOLERANCE,
                                     "reliable_backdate_share": RELIABLE_BACKDATE_SHARE}}
    slugged = ss[ss["slug_date"].notna()]
    out["sharesansar"] = {
        "rows": int(len(ss)),
        "with_creation_date_in_slug": int(len(slugged)),
        "shown_date_equals_creation": _share(slugged["backdated_days"] == 0),
        "shown_date_earlier_than_creation_by_more_than_1_day": _share(slugged["backdated_days"] > 1),
        "worst_backdating_days": int(slugged["backdated_days"].max()) if len(slugged) else None,
        "before_quarter_end": _share(ss["before_quarter_end"]),
        "by_year": {},
    }
    out["merolagani"] = {
        "rows": int(len(ml)),
        "backdated_against_previous_ids": _share(ml["id_backdated"]),
        "before_quarter_end": _share(ml["before_quarter_end"]),
        "by_year": {},
    }
    for year, part in ss.groupby(pd.to_datetime(ss["published_date"]).dt.year):
        sl = part[part["slug_date"].notna()]
        out["sharesansar"]["by_year"][str(year)] = {"rows": int(len(part)), "slugged": int(len(sl)),
                                                   "backdated_share": _share(sl["backdated_days"] > 1), "before_quarter_end": _share(part["before_quarter_end"])}
    for year, part in ml.groupby(pd.to_datetime(ml["published_date"]).dt.year):
        out["merolagani"]["by_year"][str(year)] = {"rows": int(len(part)), "backdated_share": _share(part["id_backdated"]),
                                                  "before_quarter_end": _share(part["before_quarter_end"])}
    s = ss[~ss["is_correction"] & ss["symbol"].notna()].sort_values("published_date").drop_duplicates(KEYS)
    m = ml[~ml["is_correction"] & ml["symbol"].notna()].sort_values("published_date").drop_duplicates(KEYS)
    pair = s.merge(m, on=KEYS, suffixes=("_ss", "_ml"))
    gap = np.array([(a - b).days for a, b in zip(pair["published_date_ss"], pair["published_date_ml"])])
    disagree = pair[np.abs(gap) > 1]
    gap_d = np.array([(a - b).days for a, b in zip(disagree["published_date_ss"], disagree["published_date_ml"])])
    ss_first = disagree[gap_d < 0]
    ml_first = disagree[gap_d > 0]
    out["cross_source"] = {
        "matched_reports": int(len(pair)),
        "same_day": _share(pd.Series(gap == 0)),
        "within_1_day": _share(pd.Series(np.abs(gap) <= 1)),
        "within_7_days": _share(pd.Series(np.abs(gap) <= 7)),
        "sharesansar_earlier_by_more_than_1_day": int(len(ss_first)),
        "merolagani_earlier_by_more_than_1_day": int(len(ml_first)),
        "sharesansar_earlier_and_slug_confirms": int((ss_first["slug_date"].notna() & (ss_first["backdated_days_ss"] <= 1)).sum()),
        "sharesansar_earlier_and_slug_contradicts": int((ss_first["slug_date"].notna() & (ss_first["backdated_days_ss"] > 1)).sum()),
        "sharesansar_earlier_without_slug": int(ss_first["slug_date"].isna().sum()),
        "merolagani_earlier_and_id_order_confirms": int((~ml_first["id_backdated"]).sum()),
        "merolagani_earlier_and_id_order_contradicts": int(ml_first["id_backdated"].sum()),
        "gap_days_percentiles": {str(q): float(np.percentile(gap, q)) for q in (1, 5, 25, 50, 75, 95, 99)} if len(gap) else {},
    }
    out["nepse_notices"] = {"status": "not measured", "reason": "nepalstock.com notice API answers 401 'UNAUTHORIZED ACCESS'; access control is not bypassed"}
    ss_rate = out["sharesansar"]["shown_date_earlier_than_creation_by_more_than_1_day"] or 0.0
    ml_rate = out["merolagani"]["backdated_against_previous_ids"] or 0.0
    out["verdict"] = {
        "sharesansar": "reliable" if ss_rate <= RELIABLE_BACKDATE_SHARE else "unreliable",
        "merolagani": "reliable" if ml_rate <= RELIABLE_BACKDATE_SHARE else "not reliable as shown; used only as max(shown date, median date of the previous 100 IDs)",
        "rule": "knowledge date = earliest date among item-verified sources; known from the first session strictly after it",
    }
    combined = combine(ss[ss["net_profit"].notna()], ml)
    days = pd.Series([(a - p).days for a, p in zip(combined["available_date"], combined["published_date"])])
    out["effect_on_reports_with_net_profit"] = {
        "reports": int(len(combined)),
        "earlier_than_sharesansar_date": int((days < 0).sum()),
        "same_as_sharesansar_date": int((days == 0).sum()),
        "later_than_sharesansar_date": int((days > 0).sum()),
        "median_days_earlier_when_earlier": float(-days[days < 0].median()) if (days < 0).any() else None,
        "dropped_no_verified_date": int(ss[ss["net_profit"].notna() & ~ss["is_correction"]].drop_duplicates(KEYS).shape[0] - len(combined)),
    }
    return out


def main() -> None:
    from src.database.holdout_guard import LAST_DEVELOPMENT_DAY, research_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--end", type=date.fromisoformat, default=LAST_DEVELOPMENT_DAY)
    args = parser.parse_args()
    ss, ml = load_sources(research_engine(), args.end)
    report = reliability(ss, ml)
    report["end"] = args.end.isoformat()
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("sharesansar", "merolagani", "cross_source", "verdict", "effect_on_reports_with_net_profit")}, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
