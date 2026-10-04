from __future__ import annotations

import json
from typing import Any

from src.backtest.ledger import DatabaseLedger, variant_fingerprint
from src.scorecard import model_v0, v2

FAMILY = "live_hypotheses_v0"
DECLARED_AT = "2026-10-04T01:04:22+05:45"
DECLARATION_DOC = "docs/LIVE_HYPOTHESES.md"
MODEL_PARAMETERS_HASH = "46ba8c8c7f5090ae09eba3824d4ab261fae6f618e92bdf0b1ff72d80ab4875f0"

GATE = {
    "protocol": v2.PROTOCOL_VERSION,
    "edge_min": v2.GATE_EDGE,
    "edge_lower_bound": "plain one-sided 90% > 0 and penalized alpha 0.10/K > 0, K = versions in scorecard_accuracy_v2 x 104",
    "expectancy": "> 0 at 0.5%, 1.0%, 1.5% cost; excess over same-date universe mean > 0 at 1.0%",
    "folds_positive_min": 3,
    "min_graded_calls": v2.GATE_MIN_CALLS,
    "min_distinct_dates": v2.GATE_MIN_DATES,
    "lookahead_audit": "pass",
}

HYPOTHESES: tuple[dict[str, Any], ...] = (
    {
        "id": "H1_avoid_after_bonus_book_close",
        "component": "avoid rule E2",
        "side": "avoid",
        "rule": "stock with a bonus book-close ex-session in the last 20 sessions",
        "horizons": [20],
        "status_before_live": "pre-registered event test E2 passed on 2014-06-01 to 2025-01-19 with caveats",
    },
    {
        "id": "H2_avoid_after_upper_streak_end",
        "component": "avoid rule E4",
        "side": "avoid",
        "rule": "stock whose upper-circuit close streak of 3 or more ended in the last 20 sessions",
        "horizons": [20],
        "status_before_live": "pre-registered event test E4 passed on 2014-06-01 to 2025-01-19 with caveats",
    },
    {
        "id": "H3_momentum_tilt",
        "component": "momentum tilt",
        "side": "buy",
        "rule": "model v0 calls that are not new listings: top 10 by 20-session total return, outside bear states",
        "horizons": [5, 10, 20],
        "status_before_live": "NO EVIDENCE in every replay cell (edge +1.9 to +2.1 points at 5-20 sessions)",
    },
    {
        "id": "H4_new_listing_40",
        "component": "new-listing tilt",
        "side": "buy",
        "rule": "model v0 calls carrying the new_listing situation label",
        "horizons": [40],
        "status_before_live": "found after looking at replay results (edge +8.75 points, penalized bound -6.4); a hypothesis, not evidence",
    },
)


def parameters(hypothesis: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": FAMILY,
        "declared_at": DECLARED_AT,
        "model": model_v0.NAME,
        "model_version": model_v0.VERSION,
        "model_parameters_hash": MODEL_PARAMETERS_HASH,
        "gate": GATE,
        **hypothesis,
    }


def register(ledger: Any = None) -> list[dict[str, Any]]:
    ledger = ledger or DatabaseLedger()
    out = []
    for hypothesis in HYPOTHESES:
        params = parameters(hypothesis)
        total = ledger.register_variant(FAMILY, params, f"{FAMILY} {hypothesis['id']} declared {DECLARED_AT}")
        out.append({"id": hypothesis["id"], "fingerprint": variant_fingerprint(params), "variant_trials_total": total})
    return out


def main() -> None:
    import sys

    print(json.dumps(amend() if "--amend" in sys.argv else register(), indent=2))


if __name__ == "__main__":
    main()


AMENDED_AT = "2026-10-04T11:30:26+05:45"
AMENDMENT = {
    "amended_at": AMENDED_AT,
    "model_version": model_v0.VERSION,
    "protocol": v2.PROTOCOL_VERSION,
    "reason": "deadline moved to the next NEPSE session open and promoter shares removed from the universe; no live v0 call existed",
}


def amend(ledger: Any = None) -> list[dict[str, Any]]:
    from src.scorecard.ledger import feature_hash

    ledger = ledger or DatabaseLedger()
    out = []
    for hypothesis in HYPOTHESES:
        params = {**parameters(hypothesis), **AMENDMENT, "model_version": model_v0.VERSION,
                  "model_parameters_hash": feature_hash(model_v0.PARAMETERS), "gate": {**GATE, "protocol": v2.PROTOCOL_VERSION}}
        ledger.register_variant(FAMILY, params, f"{FAMILY} {hypothesis['id']} amended {AMENDED_AT}")
        out.append({"id": hypothesis["id"], "fingerprint": variant_fingerprint(params)})
    return out
