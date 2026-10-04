from __future__ import annotations

from datetime import date
from itertools import product
from typing import Any

FAMILY = "ranker_prereg_v1"
DECLARED_AT = "2026-10-04T11:57:43+05:45"
DECLARATION_DOC = "docs/RANKER_PREREG.md"
STRATEGY = "ranker_lgbm"
VERSION = "r1"

WINDOW_START = date(2014, 6, 1)
WINDOW_END = date(2025, 1, 19)
FIRST_TEST_DAY = date(2018, 2, 18)
FOLDS: tuple[tuple[date, date], ...] = (
    (date(2018, 2, 18), date(2018, 12, 31)),
    (date(2019, 1, 1), date(2019, 12, 31)),
    (date(2020, 1, 1), date(2020, 12, 31)),
    (date(2021, 1, 1), date(2021, 12, 31)),
    (date(2022, 1, 1), date(2022, 12, 31)),
    (date(2023, 1, 1), date(2023, 12, 31)),
    (date(2024, 1, 1), date(2025, 1, 19)),
)
HORIZONS = (5, 10, 20, 40)
EMBARGO_SESSIONS = 5
WARMUP_SESSIONS = 240
INNER_VALIDATION_SHARE = 0.2
MIN_CROSS_SECTION = 30

FIXED_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "learning_rate": 0.03,
    "n_estimators": 400,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": 2026,
    "n_jobs": 4,
    "verbose": -1,
}
GRID: tuple[dict[str, int], ...] = tuple({"num_leaves": a, "min_child_samples": b} for a, b in product((15, 63), (200, 1000)))

PICKS = 10
MAX_PER_SECTOR = 3

PRICE_FEATURES = (
    "ret_1", "ret_5", "ret_20", "ret_60", "mom_120_skip5", "vol_20", "vol_60", "turnover_20", "turnover_ratio_5_60",
    "close_sma20", "close_sma50", "close_sma200", "dist_high_240", "upper_hits_20", "lower_hits_20", "traded_share_60",
    "age_sessions", "log_price",
)
SECTOR_FEATURES = ("ret_20_vs_sector", "ret_60_vs_sector", "vol_20_vs_sector", "sector_ret_20")
ACTION_FEATURES = ("since_bonus", "since_right", "since_dividend", "e2_bonus_20", "e4_streak_20", "since_dividend_declaration",
                   "declared_bonus_pct")
BROKER_FEATURES = ("broker_h1", "broker_h2", "broker_h3", "broker_h4", "broker_h5")
EARNINGS_FEATURES = ("profit_growth_yoy", "since_report", "profit_positive", "report_count")
MARKET_FEATURES = ("nepse_ret_20", "nepse_ret_60", "breadth_sma50", "market_turnover_ratio_5_60", "state_code", "rate_rising", "rate_falling")
STOCK_FEATURES = PRICE_FEATURES + SECTOR_FEATURES + ACTION_FEATURES + BROKER_FEATURES + EARNINGS_FEATURES
FEATURES = STOCK_FEATURES + MARKET_FEATURES
CAP_SESSIONS = 250


def trials() -> list[dict[str, Any]]:
    return [{"family": FAMILY, "declared_at": DECLARED_AT, "strategy": f"{STRATEGY}_h{h}", "version": VERSION, "horizon": h,
             "grid_point": g, "fixed": FIXED_PARAMS, "features": list(FEATURES), "folds": [(a.isoformat(), b.isoformat()) for a, b in FOLDS],
             "embargo": EMBARGO_SESSIONS, "picks": PICKS, "max_per_sector": MAX_PER_SECTOR}
            for h in HORIZONS for g in GRID]


def strategy_name(horizon: int) -> str:
    return f"{STRATEGY}_h{horizon}"
