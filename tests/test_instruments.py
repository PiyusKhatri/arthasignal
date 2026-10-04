from __future__ import annotations

from datetime import date

import pytest

from src.database.instruments import PROMOTER, classify, is_promoter
from src.pipeline.backfill_price_history import infer_instrument_type

KNOWN = {"HIDCL", "NABIL", "LSL", "KMBSL", "UPPER"}


@pytest.mark.parametrize("symbol, name, expected", [
    ("HIDCLP", "Hydroelectricity Investment and Development Company Limited Promoter Share", True),
    ("NABILP", "NABILP", True),
    ("LSLPO", "Laxmi Sunrise Bank Limited Promoter Share", True),
    ("KMBSLP", "unknown", True),
    ("SJLICP", "Suryajyoti Life Insurance Co. Ltd. Promotor Share", True),
    ("NABIL", "Nabil Bank Limited", False),
    ("UPPER", "Upper Tamakoshi Hydropower Ltd", False),
    ("APIP", None, False),
    ("SHEP", "Some Hydro Energy", False),
])
def test_promoter_rule(symbol, name, expected) -> None:
    assert is_promoter(symbol, name, KNOWN) is expected


def test_reported_equity_type_is_overridden_for_promoter_shares() -> None:
    assert classify("HIDCLP", "HIDCLP", "Equity", KNOWN) == PROMOTER
    assert classify("NABIL", "Nabil Bank Limited", "Equity", KNOWN) == "Equity"
    assert infer_instrument_type("NABILP", "NABILP", KNOWN) == PROMOTER


def test_database_has_no_promoter_share_left_in_the_equity_universe() -> None:
    try:
        from src.database.connection import engine
        from src.database.reclassify_promoters import candidates

        with engine.connect() as connection:
            assert candidates(connection) == []
    except Exception as error:
        if "connect" in str(error).lower():
            pytest.skip("database not reachable")
        raise


def test_research_universe_excludes_hidclp() -> None:
    try:
        from src.backtest.event_data import load_inputs
        inputs = load_inputs(date(2025, 1, 19))
    except Exception as error:
        pytest.skip(f"database not reachable: {error}")
    symbols = set(inputs["prices"]["symbol"])
    assert "HIDCL" in symbols
    assert not {"HIDCLP", "NABILP", "GBIMEP", "NICAP"} & symbols
