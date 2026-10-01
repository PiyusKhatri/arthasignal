from __future__ import annotations

from src.backtest.broker_flow_spec import (
    DEVELOPMENT_END,
    HYPOTHESES,
    MODEL_FAMILY,
    register_variants,
    variant_parameters,
)
from src.backtest.config import load_holdout_config
from src.backtest.broker_flow_spec import CONFIG_PATH
from src.backtest.ledger import InMemoryLedger, variant_fingerprint


def test_at_most_six_hypotheses_each_with_a_direction() -> None:
    assert 1 <= len(HYPOTHESES) <= 6
    assert all(spec["direction"] in {"high", "low"} for spec in HYPOTHESES.values())


def test_registration_is_idempotent_and_distinct() -> None:
    ledger = InMemoryLedger()
    assert register_variants(ledger) == len(HYPOTHESES)
    assert register_variants(ledger) == len(HYPOTHESES)
    assert {family for family, _ in ledger.variants} == {MODEL_FAMILY}
    fingerprints = {variant_fingerprint(variant_parameters(h)) for h in HYPOTHESES}
    assert len(fingerprints) == len(HYPOTHESES)


def test_config_cuts_development_before_unfinished_floorsheet() -> None:
    config = load_holdout_config(CONFIG_PATH)
    assert config.holdout_start > DEVELOPMENT_END
    assert (config.holdout_start - DEVELOPMENT_END).days == 1
