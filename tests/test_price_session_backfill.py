from __future__ import annotations

from datetime import date

from src.pipeline.backfill_price_session import is_valid_price_row
from src.scrapers.sharesansar_scraper import _parse_session_table

HEADER = (
    "<tr><th>S.No</th><th>Symbol</th><th>Conf.</th><th>Open</th><th>High</th><th>Low</th><th>Close</th>"
    "<th>LTP</th><th>Vol</th><th>Prev. Close</th><th>Turnover</th></tr>"
)


def _page(as_of: str, rows: str) -> str:
    return f"<div>As of : {as_of}</div><table>{HEADER}{rows}</table>"


def test_session_table_is_parsed_by_header_name() -> None:
    row = (
        "<tr><td>1</td><td>ADBL</td><td>46.99</td><td>304.00</td><td>329.00</td><td>304.00</td>"
        "<td>322.00</td><td>322.00</td><td>84,053.00</td><td>320.00</td><td>27,182,336.20</td></tr>"
    )
    as_of, rows = _parse_session_table(_page("2026-07-27", row), date(2026, 7, 27))

    assert as_of == date(2026, 7, 27)
    assert rows == [
        {
            "symbol": "ADBL",
            "date": date(2026, 7, 27),
            "open": 304.0,
            "high": 329.0,
            "low": 304.0,
            "close": 322.0,
            "volume": 84053,
            "turnover": 27182336.2,
            "prev_close": 320.0,
        }
    ]


def test_session_table_reports_the_source_date_not_the_requested_one() -> None:
    as_of, rows = _parse_session_table(_page("2026-07-24", ""), date(2026, 7, 25))

    assert as_of == date(2026, 7, 24)
    assert rows == []


def test_invalid_price_rows_are_rejected() -> None:
    good = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100}

    assert is_valid_price_row(good)
    assert is_valid_price_row({**good, "close": 11.5})
    assert not is_valid_price_row({**good, "low": 0.0})
    assert not is_valid_price_row({**good, "high": 8.0})
    assert not is_valid_price_row({**good, "volume": 0})
    assert not is_valid_price_row({**good, "close": None})
