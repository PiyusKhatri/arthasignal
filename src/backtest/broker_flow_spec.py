from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "broker-flow-prereg-v1"
MODEL_FAMILY = "broker_flow_prereg_2026_10"
CONFIG_PATH = Path(__file__).with_name("broker_flow_config.json")

DEVELOPMENT_START = date(2014, 6, 1)
DEVELOPMENT_END = date(2025, 1, 19)
FIRST_REAL_OPEN_SESSION = date(2018, 2, 18)
SEASONED_IF_FIRST_PRICE_ON_OR_BEFORE = date(2014, 7, 1)

MIN_TRADES_ON_SIGNAL_DAY = 5
MIN_TRADED_SESSIONS_OF_LAST_20 = 15
NEW_LISTING_SESSIONS = 60
MIN_ELIGIBLE_PER_DATE = 25
QUINTILE = 0.20
HORIZON_SESSIONS = 20
GRACE_SESSIONS = 3
COST_LEVELS = (0.005, 0.010, 0.015)
PRIMARY_COST = 0.010
STRESS_COST = 0.015
BASE_ALPHA = 0.05
CLUSTER_BLOCK_SESSIONS = 20
MIN_FOLD_SIGNAL_DATES = 50
MIN_FOLDS_POSITIVE = 3

H3_MIN_HISTORY_DAYS = 10
H4_MOVE_THRESHOLD = 0.20
H4_LOOKBACK_SESSIONS = 500
H4_EMBARGO_SESSIONS = 5
H4_REFRESH_SESSIONS = 20
H4_TOP_BROKERS = 10
H4_MIN_EVENTS = 100

HYPOTHESES: dict[str, dict[str, Any]] = {
    "H1": {
        "name": "top5_net_buy_share",
        "window_sessions": 5,
        "top_brokers": 5,
        "direction": "high",
        "description": "Net buy quantity of the 5 largest net-buying brokers over 5 sessions divided by total quantity",
    },
    "H2": {
        "name": "buy_minus_sell_hhi",
        "window_sessions": 5,
        "direction": "high",
        "description": "Buy-side broker HHI minus sell-side broker HHI over 5 sessions",
    },
    "H3": {
        "name": "buy_hhi_change_vs_20d",
        "window_sessions": 1,
        "baseline_sessions": 20,
        "min_history_days": H3_MIN_HISTORY_DAYS,
        "direction": "high",
        "description": "Day-t buy-side HHI minus mean daily buy-side HHI over t-20..t-1",
    },
    "H4": {
        "name": "early_buyer_imbalance",
        "window_sessions": 5,
        "move_threshold": H4_MOVE_THRESHOLD,
        "lookback_sessions": H4_LOOKBACK_SESSIONS,
        "embargo_sessions": H4_EMBARGO_SESSIONS,
        "refresh_sessions": H4_REFRESH_SESSIONS,
        "top_brokers": H4_TOP_BROKERS,
        "min_events": H4_MIN_EVENTS,
        "direction": "high",
        "description": "Net quantity of historically early-buying brokers over 5 sessions divided by total quantity",
    },
    "H5": {
        "name": "close_vs_top5_buyer_vwap",
        "window_sessions": 20,
        "top_brokers": 5,
        "direction": "low",
        "description": "Close over the buy VWAP of the 5 largest net buyers over 20 sessions, minus 1",
    },
}


def variant_parameters(hypothesis_id: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "hypothesis": hypothesis_id,
        **HYPOTHESES[hypothesis_id],
        "portfolio_quantile": QUINTILE,
        "horizon_sessions": HORIZON_SESSIONS,
        "entry": "next_open_from_2018_02_18_else_next_close",
        "development_end": DEVELOPMENT_END.isoformat(),
        "cost_levels": list(COST_LEVELS),
    }


def register_variants(ledger: Any) -> int:
    count = 0
    for hypothesis_id, spec in HYPOTHESES.items():
        count = ledger.register_variant(
            MODEL_FAMILY,
            variant_parameters(hypothesis_id),
            f"{PROTOCOL_VERSION} {hypothesis_id}: {spec['description']}",
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
