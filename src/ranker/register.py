from __future__ import annotations

import argparse
import json

from src.backtest.ledger import DatabaseLedger, variant_fingerprint
from src.scorecard import v2


def register(kind: str) -> dict:
    from src.ranker import meta_spec
    from src.ranker import spec as ranker_spec

    module = ranker_spec if kind == "ranker" else meta_spec
    ledger = DatabaseLedger()
    out = []
    for trial in module.trials():
        ledger.register_variant(module.FAMILY, trial, f"{module.FAMILY} {trial['strategy']} {trial.get('grid_point', '')} declared {module.DECLARED_AT}")
        ledger.register_variant(v2.VARIANT_FAMILY, {"protocol": v2.PROTOCOL_VERSION, **trial},
                                f"{v2.PROTOCOL_VERSION} strategy {trial['strategy']} {trial['version']} {trial.get('grid_point', '')}")
        out.append({"strategy": trial["strategy"], "grid_point": trial.get("grid_point"), "fingerprint": variant_fingerprint(trial)[:16]})
    return {"family": module.FAMILY, "declared_at": module.DECLARED_AT, "trials": out}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("ranker", "meta"))
    print(json.dumps(register(parser.parse_args().kind), indent=1))


if __name__ == "__main__":
    main()
