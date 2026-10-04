from __future__ import annotations

import json

from src.database import instrument_fixes as fx
from src.pipeline.backfill_price_session import is_valid_price_row


def evidence():
    return json.loads(fx.EVIDENCE_PATH.read_text())


def test_every_assigned_sector_has_a_name_and_an_nrb_class():
    for item in evidence()["sector_assignments"]:
        if item["sector"] is None:
            continue
        assert item["class_source"]["url"].startswith("https://www.nrb.org.np/")
        assert fx.NRB_CLASS_SECTOR[item["class_source"]["class"]] == item["sector"]
        assert item["name_source"]["title"]


def test_blank_rows_are_mapped_only_on_exact_evidence():
    for row in evidence()["blank_symbol_prices"]:
        assert row["symbol"] is None or row["method"].startswith(("floorsheet: same volume", "sharesansar blank row"))
        if row["method"].startswith("sharesansar"):
            assert "(1 candidates)" in row["method"]


def test_truncated_symbols_need_floorsheet_agreement():
    items = {i["short"]: i for i in evidence()["truncated_symbols"]}
    assert items["NIFRAUR85/"]["floorsheet_days_agree"] / items["NIFRAUR85/"]["floorsheet_days_checked"] >= 0.9
    assert items["NICAD 85/8"]["floorsheet_days_agree"] == 0


def test_class_header_lookup():
    textual = 'Class: "A" x Bank one Class: "D" y Laghubitta two'
    assert fx._class_at(textual, textual.index("Bank one")) == "A"
    assert fx._class_at(textual, textual.index("Laghubitta")) == "D"
    assert fx._class_at(textual, 0) is None


def test_blank_symbol_rows_are_rejected_at_ingest():
    assert not is_valid_price_row({"symbol": "", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1})
