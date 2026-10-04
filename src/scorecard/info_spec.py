from __future__ import annotations

from datetime import date
from typing import Any

from src.scorecard import v2

PROTOCOL_VERSION = "info-prereg-v1"
FAMILY = "info_prereg_v1"
DECLARED_AT = "2026-10-04T09:41:54+05:45"
DEVELOPMENT_START = date(2014, 6, 1)
DEVELOPMENT_END = date(2025, 1, 19)
GRADE_VERSION = "info-v1"
QUARTILE_LOOKBACK_SESSIONS = 250
QUARTILE_MIN_REPORTS = 40
MIN_PRIOR_PROFIT_ABS = 1_000_000.0
BOOK_CLOSE_MIN_GAP_SESSIONS = 12
BANK_SECTOR = "Commercial Banks"

HYPOTHESES: dict[str, dict[str, Any]] = {
    "I1": {
        "name": "yoy_profit_growth_top_quartile_20",
        "event": "quarterly report",
        "rule": "YoY net profit growth in the top quartile of the trailing 250-session distribution",
        "horizon": 20,
    },
    "I2": {
        "name": "yoy_profit_growth_top_quartile_40",
        "event": "quarterly report",
        "rule": "same selection as I1, held 40 sessions",
        "horizon": 40,
    },
    "I3": {
        "name": "profit_turnaround_20",
        "event": "quarterly report",
        "rule": "net profit > 0 this quarter after a net loss in the same quarter of the prior fiscal year",
        "horizon": 20,
    },
    "I4": {
        "name": "dividend_declaration_non_decreasing_10",
        "event": "dividend declaration",
        "rule": "declared cash + bonus > 0 and >= the symbol's previous declared total",
        "horizon": 10,
    },
    "I5": {
        "name": "bonus_announcement_to_book_close_10",
        "event": "dividend declaration",
        "rule": "declared bonus > 0 and book close at least 12 sessions after the knowledge session",
        "horizon": 10,
    },
    "I6": {
        "name": "bank_yoy_profit_growth_top_quartile_20",
        "event": "quarterly report",
        "rule": "I1 restricted to Commercial Banks, with the all-sector quartile threshold",
        "horizon": 20,
    },
}

DIAGNOSTICS = {
    "all_reports_20": "every quarterly report with a computable YoY growth, held 20 sessions (control for I1)",
    "all_declarations_10": "every dividend declaration, held 10 sessions (control for I4)",
}


def strategy_name(hypothesis_id: str) -> str:
    return f"info_{hypothesis_id.lower()}_{HYPOTHESES[hypothesis_id]['name']}"


def variant_parameters(hypothesis_id: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "declared_at": DECLARED_AT,
        "hypothesis": hypothesis_id,
        **HYPOTHESES[hypothesis_id],
        "development": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "quartile_lookback_sessions": QUARTILE_LOOKBACK_SESSIONS,
        "quartile_min_reports": QUARTILE_MIN_REPORTS,
        "min_prior_profit_abs": MIN_PRIOR_PROFIT_ABS,
        "book_close_min_gap_sessions": BOOK_CLOSE_MIN_GAP_SESSIONS,
        "gate": {"protocol": v2.PROTOCOL_VERSION, "edge_min": v2.GATE_EDGE, "min_calls": v2.GATE_MIN_CALLS,
                 "min_dates": v2.GATE_MIN_DATES, "min_windows": v2.MIN_WINDOWS},
    }


def register() -> list[dict[str, Any]]:
    from src.backtest.ledger import DatabaseLedger, variant_fingerprint

    ledger = DatabaseLedger()
    out = []
    for hypothesis_id in HYPOTHESES:
        params = variant_parameters(hypothesis_id)
        total = ledger.register_variant(FAMILY, params, f"{FAMILY} {hypothesis_id} {HYPOTHESES[hypothesis_id]['name']} declared {DECLARED_AT}")
        out.append({"id": hypothesis_id, "fingerprint": variant_fingerprint(params), "variant_trials_total": total})
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(register(), indent=2))
