from __future__ import annotations

from datetime import date
from typing import Any

from src.ranker import spec as ranker_spec

FAMILY = "meta_prereg_v1"
DECLARED_AT = "2026-10-04T11:57:50+05:45"
DECLARATION_DOC = "docs/META_PREREG.md"
STRATEGY = "meta_lgbm"
VERSION = "m1"
PRIMARY_VERSION = ranker_spec.VERSION
HORIZONS = ranker_spec.HORIZONS
FOLDS: tuple[tuple[date, date], ...] = ranker_spec.FOLDS[1:]
EMBARGO_SESSIONS = 5
KEEP_QUANTILE = 0.70
MIN_TRAINING_CALLS = 300
LOGISTIC: dict[str, Any] = {"C": 1.0, "max_iter": 2000, "solver": "lbfgs"}
META_FEATURES = (
    "score", "score_rank", "gap_to_11th", "pick_rank", "state_code", "breadth_sma50", "nepse_ret_20",
    "vol_20", "turnover_20", "ret_20", "e2_bonus_20", "e4_streak_20",
)


def trials() -> list[dict[str, Any]]:
    return [{"family": FAMILY, "declared_at": DECLARED_AT, "strategy": f"{STRATEGY}_h{h}", "version": VERSION, "horizon": h,
             "primary": f"{ranker_spec.STRATEGY}_h{h} {PRIMARY_VERSION}", "model": "logistic regression", "params": LOGISTIC,
             "features": list(META_FEATURES), "keep_quantile": KEEP_QUANTILE, "folds": [(a.isoformat(), b.isoformat()) for a, b in FOLDS]}
            for h in HORIZONS]


def strategy_name(horizon: int) -> str:
    return f"{STRATEGY}_h{horizon}"
