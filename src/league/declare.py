from __future__ import annotations

import json
from typing import Any

from src.backtest.ledger import DatabaseLedger, variant_fingerprint
from src.league import bots as league_bots
from src.league.bots import BOTS
from src.scorecard import v2
from src.scorecard.ledger import feature_hash

FAMILY = league_bots.LEAGUE
DECLARED_AT = "2026-10-04T11:30:26+05:45"
SUPERSEDES = {"version": "b1", "declared_at": "2026-10-04T10:35:04+05:45", "reason": "protocol v2.1 deadline and promoter shares removed from the universe before any live call"}
DECLARATION_DOC = "docs/PAPER_BOT_LEAGUE.md"
FIRST_LIVE_SIGNAL_DATE = "first NEPSE session written by src.league.run after this declaration"

GATE = {
    "protocol": v2.PROTOCOL_VERSION,
    "edge_min": v2.GATE_EDGE,
    "edge_lower_bound": "plain one-sided 90% > 0 and penalized alpha 0.10/K > 0, K = versions in scorecard_accuracy_v2 x 104",
    "expectancy": "> 0 at 0.5%, 1.0%, 1.5% cost; excess over same-date universe mean > 0 at 1.0%; avoid side: avoided stocks trail the universe",
    "folds_positive_min": 3,
    "min_graded_calls": v2.GATE_MIN_CALLS,
    "min_distinct_dates": v2.GATE_MIN_DATES,
    "min_windows": v2.MIN_WINDOWS,
    "lookahead_audit": "pass",
    "mode": "live calls only",
}


def parameters(bot: league_bots.Bot) -> dict[str, Any]:
    params = league_bots.bot_parameters(bot)
    return {
        "family": FAMILY,
        "declared_at": DECLARED_AT,
        "first_live_signal_date": FIRST_LIVE_SIGNAL_DATE,
        "bot": bot.name,
        "bot_version": bot.version,
        "bot_parameters_hash": feature_hash(params),
        "description": bot.description,
        "supersedes": SUPERSEDES,
        "gate": GATE,
        **params,
    }


def register(ledger: Any = None) -> list[dict[str, Any]]:
    ledger = ledger or DatabaseLedger()
    out = []
    for bot in BOTS:
        params = parameters(bot)
        total = ledger.register_variant(FAMILY, params, f"{FAMILY} {bot.name} {bot.version} declared {DECLARED_AT}")
        out.append({"bot": bot.name, "version": bot.version, "fingerprint": variant_fingerprint(params),
                    "parameters_hash": params["bot_parameters_hash"], "variant_trials_total": total})
    return out


def main() -> None:
    from src.database.connection import engine
    from src.league.run import apply_league_schema, register_bots

    apply_league_schema(engine)
    models = register_bots(engine, ledger=DatabaseLedger())
    print(json.dumps({"declared_at": DECLARED_AT, "hypotheses": register(), "models": models}, indent=2, default=str))


if __name__ == "__main__":
    main()
