from __future__ import annotations

import json

from sqlalchemy import text

from src.backtest.ledger import variant_fingerprint
from src.simulation.protocol import load


def protocol_parameters() -> dict:
    p = load()
    return {"protocol": p.version, "config_sha256": p.sha256}


def register() -> dict:
    from src.database.holdout_guard import research_engine

    p = load()
    family = str(p.raw["protocol"]["trial_family"])
    parameters = protocol_parameters()
    fingerprint = variant_fingerprint(parameters)
    description = f"{p.version} config sha256 {p.sha256} locked {p.raw['protocol']['locked_on']}"
    with research_engine().begin() as connection:
        user = connection.execute(text("SELECT current_user")).scalar_one()
        inserted = connection.execute(
            text(
                "INSERT INTO backtest_variant_trials (model_family, variant_fingerprint, description, created_at) "
                "VALUES (:family, :fingerprint, :description, timezone('utc', now())) "
                "ON CONFLICT (model_family, variant_fingerprint) DO NOTHING"
            ),
            {"family": family, "fingerprint": fingerprint, "description": description},
        ).rowcount
        in_family = connection.execute(
            text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = :family"), {"family": family}
        ).scalar_one()
        total = connection.execute(text("SELECT count(*) FROM backtest_variant_trials")).scalar_one()
    return {
        "role": user,
        "family": family,
        "fingerprint": fingerprint,
        "inserted": inserted,
        "family_total": int(in_family),
        "trials_total": int(total),
        **parameters,
    }


if __name__ == "__main__":
    print(json.dumps(register(), indent=1))
