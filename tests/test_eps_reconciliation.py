from __future__ import annotations

from src.scrapers import eps_reconciliation as er


def test_classify_known_patterns() -> None:
    assert er.classify(36.95, 36.95, 5069936.0, 137213760.0, 5.0) == er.MATCH
    assert er.classify(35.19, 36.95, 5069936.0, 137213760.0, 5.0) == er.BONUS_RESCALED
    assert er.classify(17.49, 15.44, 2113878.0, 120862115.64, 3.0) == er.REPORTED_WEIGHTED
    assert er.classify(28.36, 27.74, 7905796.0, 270569970.0, 5.0) == er.NEITHER
    assert er.classify(21.24, 23.36, None, None, None) == er.NO_SHARES


def test_bonus_ratio_without_share_count() -> None:
    assert er.classify(16.0, 18.4, None, float("nan"), 15.0) == er.BONUS_RATIO_ONLY
    assert er.classify(1.58, 1.59, None, float("nan"), None) == er.NO_SHARES
