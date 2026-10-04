from __future__ import annotations

import inspect
from datetime import date

import pytest

from src.database.instruments import equity_sector, equity_sectors, normalize_sector
from src.pipeline import data_quality, market_pulse


def test_non_equity_instruments_never_get_an_equity_sector() -> None:
    assert normalize_sector("Mutual Funds", "Commercial Banks") == "Mutual Fund"
    assert normalize_sector("Non-Convertible Debentures", "Finance") == "Debenture"
    assert normalize_sector("Equity", "Commercial Banks") == "Commercial Banks"
    assert normalize_sector("Mutual Funds", None) is None
    assert normalize_sector("Weird Type", "Hydro Power") == "Non-Equity"
    assert equity_sector("Mutual Funds", "Commercial Banks") is None
    assert equity_sector("Equity", "Hydro Power") == "Hydro Power"
    assert len(equity_sectors()) == 12


def test_every_market_pulse_sector_query_filters_equity() -> None:
    offenders = []
    for name, fn in inspect.getmembers(market_pulse, inspect.isfunction):
        if fn.__module__ != market_pulse.__name__:
            continue
        source = inspect.getsource(fn)
        if ("c.sector" in source or "Company.sector" in source or "GROUP BY sector" in source) and "instrument_type" not in source:
            offenders.append(name)
    assert offenders == []


def test_floorsheet_by_sector_query_excludes_funds() -> None:
    assert "c.instrument_type = 'Equity'" in inspect.getsource(market_pulse._load_floorsheet_by_sector)


def test_loud_check_raises_when_a_fund_has_an_equity_sector(monkeypatch) -> None:
    monkeypatch.setattr(data_quality, "_check_nonequity_equity_sector",
                        lambda latest: {"flagged": True, "count": 1, "symbols": ["CSY"]})
    with pytest.raises(data_quality.NonEquitySectorError, match="CSY"):
        data_quality.assert_no_nonequity_equity_sector()


def test_database_has_no_non_equity_symbol_with_an_equity_sector() -> None:
    try:
        from src.database.connection import engine
        from src.database.instruments import nonequity_with_equity_sector

        with engine.connect() as connection:
            rows = nonequity_with_equity_sector(connection)
    except Exception as error:
        pytest.skip(f"database not reachable: {error}")
    assert rows == []


def test_research_universe_and_its_sectors_contain_equities_only() -> None:
    try:
        from src.backtest.event_data import load_inputs, load_panel

        inputs = load_inputs(date(2025, 1, 19))
    except Exception as error:
        pytest.skip(f"database not reachable: {error}")
    companies = inputs["companies"].set_index("symbol")
    types = set(companies.loc[sorted(set(inputs["prices"]["symbol"])), "instrument_type"])
    assert types == {"Equity"}
    assert not {"CSY", "H8020", "NICFC", "SIGS2", "LSH12"} & set(inputs["prices"]["symbol"])
    panel = load_panel(inputs)
    assert {s for s in panel.sector if s is not None} <= equity_sectors()
