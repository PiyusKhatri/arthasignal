from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from src.database import holdout_guard as hg


def test_parameters_inside_the_holdout_raise() -> None:
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT * FROM daily_prices WHERE date <= :e", {"e": date(2025, 9, 30)})
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT 1 WHERE x = %(a)s", {"a": datetime(2026, 1, 2, 10, 0)})
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT 1", [pd.Timestamp("2026-03-01")])
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT 1", {"d": "2025-10-01"})
    hg.check_statement("SELECT * FROM daily_prices WHERE date <= :e", {"e": date(2025, 9, 29)})


def test_literals_inside_the_holdout_raise_except_a_strict_upper_bound() -> None:
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT * FROM daily_prices WHERE date >= '2025-09-30'", None)
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT * FROM daily_prices WHERE date = '2026-01-05'", None)
    hg.check_statement("SELECT * FROM t WHERE published_date < DATE '2025-09-30'", None)
    hg.check_statement("SELECT * FROM daily_prices WHERE date <= '2025-09-29'", None)


def test_only_named_reasons_can_open_the_holdout() -> None:
    with pytest.raises(hg.HoldoutQueryViolation):
        with hg.allow("curiosity"):
            pass
    with hg.allow("final_evaluation"):
        hg.check_statement("SELECT 1", {"e": date(2026, 1, 1)})
        assert hg.allowed_reason() == "final_evaluation"
    assert hg.allowed_reason() is None
    with pytest.raises(hg.HoldoutQueryViolation):
        hg.check_statement("SELECT 1", {"e": date(2026, 1, 1)})


def test_research_engine_raises_and_hides_holdout_rows() -> None:
    engine = hg.research_engine()
    with engine.connect() as connection:
        with pytest.raises(hg.HoldoutQueryViolation):
            connection.execute(text("SELECT count(*) FROM daily_prices WHERE date >= :d"), {"d": date(2025, 9, 30)})
    with engine.connect() as connection:
        latest = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar()
        guard = connection.execute(text(f"SELECT current_setting('{hg.GUC}', true)")).scalar()
    assert guard == "on"
    assert latest is None or latest < hg.HOLDOUT_START


def test_final_evaluation_opens_the_guard(monkeypatch) -> None:
    from src.backtest import holdout as h

    seen = {}

    class Ledger:
        def start_holdout_call(self, *a):
            return 1, 1

        def variant_count(self):
            seen["reason"] = hg.allowed_reason()
            return 0

        def finish_holdout_call(self, *a):
            pass

    monkeypatch.setattr(h, "selection_report", lambda *a, **k: {})
    sealed = h.SealedHoldout([], type("C", (), {"holdout_start": date(2025, 9, 30)})())
    h.final_evaluation(model_id="m", select=lambda rows: [], holdout=sealed, ledger=Ledger(), requested_by="t", reason="test")
    assert seen["reason"] == "final_evaluation"
    assert hg.allowed_reason() is None


def test_floorsheet_file_listing_refuses_holdout_dates(tmp_path) -> None:
    from src.backtest import broker_flow_features as bf

    with pytest.raises(hg.HoldoutQueryViolation):
        bf.floorsheet_files(tmp_path, end=date(2025, 10, 1))
    assert bf.floorsheet_files(tmp_path, end=date(2025, 1, 19)) == []
