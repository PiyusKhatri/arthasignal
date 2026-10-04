from __future__ import annotations

from src.scrapers import quarterly_capture as qc


def test_statement_hash_changes_only_with_content() -> None:
    a = {"column_label": "4th Quarter 2082/2083", "statement": {"keymetrics": {"EPS": 1.0}}}
    b = {"column_label": "4th Quarter 2082/2083", "statement": {"keymetrics": {"EPS": 1.0}}}
    c = {"column_label": "4th Quarter 2082/2083", "statement": {"keymetrics": {"EPS": 1.1}}}
    d = {"column_label": "1st Quarter 2083/2084", "statement": {"keymetrics": {"EPS": 1.0}}}
    assert qc.statement_hash(a) == qc.statement_hash(b)
    assert len({qc.statement_hash(x) for x in (a, c, d)}) == 3
