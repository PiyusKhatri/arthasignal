from __future__ import annotations

from src.scrapers.sector_sources import canonical, decide, parse


def test_labels_map_only_to_exact_equity_sectors_or_known_non_equity_labels() -> None:
    assert canonical("Life Insurance") == ("equity_sector", "Life Insurance")
    assert canonical(" commercial  banks ") == ("equity_sector", "Commercial Banks")
    assert canonical("Development Bank") == ("equity_sector", "Development Banks")
    assert canonical("Merged") == ("unknown", None)
    assert canonical(None) == ("unknown", None)
    assert canonical("Corporate Debenture") == ("non_equity", "Non-Convertible Debentures")
    assert canonical("Banking") == ("unrecognized", None)


def test_decisions_never_guess() -> None:
    assert decide({"sharesansar": "Merged", "merolagani": "Life Insurance"}) == {
        "result": "assigned", "sector": "Life Insurance", "detail": {"merolagani": "Life Insurance"}}
    assert decide({"sharesansar": "Finance", "merolagani": "Finance"})["sector"] == "Finance"
    assert decide({"sharesansar": "Finance", "merolagani": "Development Banks"})["result"] == "sources_disagree"
    assert decide({"sharesansar": "Corporate Debentures", "merolagani": "Corporate Debenture"})["result"] == "source_says_non_equity"
    assert decide({"sharesansar": None, "merolagani": None})["result"] == "unknown"
    assert decide({"sharesansar": "Merged", "merolagani": "Banking"})["result"] == "unrecognized_label"


def test_page_parsers() -> None:
    assert parse("sharesansar", '<title>X</title><div id="sector" style="display: none;">Merged</div>')["sector_raw"] == "Merged"
    page = "<title>merolagani - Prime Life</title><tr><th>Sector </th><td class='x'>\n Life Insurance </td></tr>"
    assert parse("merolagani", page) == {"sector_raw": "Life Insurance", "page_title": "merolagani - Prime Life"}


def test_source_aliases_apply_only_to_their_source() -> None:
    assert canonical("Development Bank Limited", "merolagani") == ("equity_sector", "Development Banks")
    assert canonical("Non-Life Insurance", "merolagani") == ("equity_sector", "Non Life Insurance")
    assert canonical("Development Bank Limited", "sharesansar") == ("unrecognized", None)
    assert decide({"sharesansar": "Merged", "merolagani": "Non-Life Insurance"})["sector"] == "Non Life Insurance"

