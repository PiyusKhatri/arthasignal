from __future__ import annotations

import pytest

from src.scrapers import quarterly_reports_collector as q

TAB = """
<ul class="nav nav-tabs"><li><a href="#balance">BALANCE SHEET</a></li></ul>
<div class="tab-content">
<div id="balance" class="tab-pane fade in active"><table><thead><tr><th>Particulars (Rs. in '000)</th><th>2nd Quarter 2081/2082</th></tr></thead>
<tbody><tr><td>Share capital</td><td>1,000.00</td></tr><tr><td>Reserves</td><td>(250.50)</td></tr><tr><td>Total equity</td><td>749.50</td></tr></tbody></table></div>
<div id="profitloss" class="tab-pane fade"><table><thead><tr><th>Particulars</th><th>2nd Quarter 2081/2082</th></tr></thead>
<tbody><tr><td>Profit for the period</td><td>120.00</td></tr></tbody></table></div>
<div id="keymetrics" class="tab-pane fade"><table><thead><tr><th>Particulars</th><th>2nd Quarter 2081/2082</th></tr></thead>
<tbody><tr><td>Basic Earnings Per Share(Annualized EPS)</td><td>24.00</td></tr><tr><td>Net Worth Per Share</td><td>74.95</td></tr></tbody></table></div>
</div>
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2082/83", "2082/2083"), ("fiscal year 2081/2082", "2081/2082"), ("2079-80", "2079/2080"), ("2082/85", None), ("none", None)],
)
def test_normalize_fiscal_year(raw, expected) -> None:
    assert q.normalize_fiscal_year(raw) == expected


def test_parse_report_title_profit_loss_and_units() -> None:
    profit = q.parse_report_title("Nabil Bank Limited has posted a net profit of Rs 3.24 billion and published its 2nd quarter company analysis of the fiscal year 2081/82.")
    assert (profit["quarter"], profit["fiscal_year"], profit["net_profit"]) == (2, "2081/2082", 3.24e9)
    loss = q.parse_report_title("ABC Hydropower Limited has posted a net loss of Rs 1,250.5 thousand and published its first quarter report of the fiscal year 2080/81")
    assert (loss["quarter"], loss["net_profit"]) == (1, -1250500.0)
    correction = q.parse_report_title("XYZ Limited has made correction on its provisional financial statement for the fourth quarter of the fiscal year 2082/83")
    assert correction["is_correction"] and correction["net_profit"] is None and correction["quarter"] == 4


def test_only_quarterly_report_titles_are_kept() -> None:
    assert q.is_quarterly_report_title("XYZ Limited has published its provisional financial statement for the third quarter of the fiscal year 2081/82")
    assert not q.is_quarterly_report_title("XYZ Limited is calling its 12th AGM on 2025-01-10")
    assert not q.is_quarterly_report_title("The existing promoter of XYZ is selling 25,000 shares")


def test_parse_quarterly_tab() -> None:
    parsed = q.parse_quarterly_tab(TAB)
    assert (parsed["fiscal_year"], parsed["quarter"]) == ("2081/2082", 2)
    assert parsed["eps"] == 24.0 and parsed["net_worth_per_share"] == 74.95
    assert parsed["reserves_thousands"] == -250.5 and parsed["share_capital_thousands"] == 1000.0
    assert parsed["profit_for_period_thousands"] == 120.0 and parsed["total_equity_thousands"] == 749.5
    assert q.parse_quarterly_tab("<div>No data available</div>") is None


def test_name_key_matches_listing_names() -> None:
    assert q._name_key("Nabil Bank Limited") == q._name_key("NABIL BANK LTD.")
    assert q._name_key("People's Power Limited") == "people s power"
