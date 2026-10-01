from __future__ import annotations

from src.backtest.event_spec import HYPOTHESES, MODEL_FAMILY, register_variants, variant_parameters
from src.backtest.ledger import InMemoryLedger, variant_fingerprint


def test_at_most_eight_hypotheses_with_fixed_directions() -> None:
    assert 1 <= len(HYPOTHESES) <= 8
    assert {spec["direction"] for spec in HYPOTHESES.values()} <= {"long", "avoid", "timing"}


def test_registration_is_idempotent_and_fingerprints_are_distinct() -> None:
    ledger = InMemoryLedger()
    assert register_variants(ledger) == len(HYPOTHESES)
    assert register_variants(ledger) == len(HYPOTHESES)
    assert {family for family, _ in ledger.variants} == {MODEL_FAMILY}
    assert len({variant_fingerprint(variant_parameters(h)) for h in HYPOTHESES}) == len(HYPOTHESES)
