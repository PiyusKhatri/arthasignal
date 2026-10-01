from __future__ import annotations

import argparse
from datetime import date
from typing import Any

PROTOCOL_VERSION = "event-prereg-v1"
MODEL_FAMILY = "event_prereg_2026_10"

DEVELOPMENT_START = date(2014, 6, 1)
DEVELOPMENT_END = date(2025, 1, 19)
BASE_ALPHA = 0.05
COST_LEVELS = (0.005, 0.010, 0.015)
PRIMARY_COST = 0.010
STRESS_COST = 0.015
FOLDS = 4
MIN_FOLD_EVENT_DATES = 20
MIN_FOLDS_CONSISTENT = 3
LIQUIDITY_LOOKBACK = 20
LIQUIDITY_MIN_TRADED = 10
SEASONED_SESSIONS = 60
AVOID_MIN_ABNORMAL = -0.010
DEDUPE_SESSIONS = 20

MARKET_SMA = 200
MARKET_BREADTH_MIN = 0.50
MARKET_SWITCH_COST_ROUND_TRIP = 0.010
MARKET_STRESS_SWITCH_COST = 0.015
MARKET_DRAWDOWN_RATIO_MAX = 0.75
MARKET_BOOTSTRAP_BLOCK = 20
MARKET_BOOTSTRAP_REPS = 2000
MARKET_BOOTSTRAP_SEED = 20261001
ANNUALIZATION = 240

HYPOTHESES: dict[str, dict[str, Any]] = {
    "E1": {
        "name": "book_close_run_up",
        "direction": "long",
        "events": "book_close with a bonus (bonus_only or bonus_and_cash)",
        "knowledge": "ex_session minus 16 sessions (book-close date assumed public by then)",
        "knowledge_offset_from_ex": -16,
        "hold_sessions": 15,
        "seasoned": True,
    },
    "E2": {
        "name": "post_book_close_drift",
        "direction": "avoid",
        "events": "book_close with a bonus (bonus_only or bonus_and_cash)",
        "knowledge": "ex_session close",
        "knowledge_offset_from_ex": 0,
        "hold_sessions": 20,
        "seasoned": True,
    },
    "E3": {
        "name": "new_listing_after_initial_run",
        "direction": "long",
        "events": "new listing (merger symbols excluded); first own trading day from day 2 whose close is below the upper-circuit threshold, within 60 market sessions of listing",
        "knowledge": "that session's close",
        "max_sessions_after_listing": 60,
        "hold_sessions": 60,
        "seasoned": False,
    },
    "E4": {
        "name": "circuit_streak_end_reversal",
        "direction": "avoid",
        "events": "upper-circuit close streak of at least 3 that ends, outside the first 60 sessions after first price",
        "knowledge": "close of the first traded session that does not close at the upper limit",
        "min_streak": 3,
        "hold_sessions": 20,
        "seasoned": True,
    },
    "E5": {
        "name": "no_news_volume_up_20",
        "direction": "long",
        "events": "volume at least 5x the 60-session median, up day, no corporate action within 20 sessions either side, at least 60 sessions since first price; one event per symbol per 20 sessions",
        "knowledge": "event session close",
        "hold_sessions": 20,
        "seasoned": True,
    },
    "E6": {
        "name": "market_trend_breadth_in_out",
        "direction": "timing",
        "rule": "IN the NEPSE Index when its close is above its 200-session SMA and more than 50% of traded equities close above their 50-session SMA; otherwise OUT earning the last published T-bill rate (0 before it is known)",
        "signal": "close of t, position from t+1",
        "switch_cost_round_trip": MARKET_SWITCH_COST_ROUND_TRIP,
    },
    "E7": {
        "name": "no_news_volume_up_60",
        "direction": "long",
        "events": "same events as E5",
        "knowledge": "event session close",
        "hold_sessions": 60,
        "seasoned": True,
    },
    "E8": {
        "name": "no_news_volume_up_120",
        "direction": "long",
        "events": "same events as E5",
        "knowledge": "event session close",
        "hold_sessions": 120,
        "seasoned": True,
    },
}


def variant_parameters(hypothesis_id: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "hypothesis": hypothesis_id,
        **HYPOTHESES[hypothesis_id],
        "development_end": DEVELOPMENT_END.isoformat(),
        "cost_levels": list(COST_LEVELS),
        "alpha": BASE_ALPHA / len(HYPOTHESES),
    }


def register_variants(ledger: Any) -> int:
    count = 0
    for hypothesis_id, spec in HYPOTHESES.items():
        count = ledger.register_variant(
            MODEL_FAMILY, variant_parameters(hypothesis_id), f"{PROTOCOL_VERSION} {hypothesis_id}: {spec['name']}"
        )
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args()
    if args.register:
        from src.backtest.ledger import DatabaseLedger

        print(f"variants in ledger: {register_variants(DatabaseLedger())}")


if __name__ == "__main__":
    main()
