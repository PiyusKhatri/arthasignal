from __future__ import annotations

from datetime import date, datetime

from src.pipeline import backfill_calendar
from src.pipeline.market_hours_guard import NPT_OFFSET
from src.pipeline.sync_market_data import _history_years_for_window, _should_refresh_eod


def test_unexplained_false_weekday_does_not_block_eod_ingestion(monkeypatch) -> None:
    day = date(2026, 8, 20)

    monkeypatch.setattr(backfill_calendar, "_confirmed_market_data_exists", lambda _day: False)
    monkeypatch.setattr(backfill_calendar, "_calendar_status", lambda _day: (False, None))
    monkeypatch.setattr(backfill_calendar, "_known_non_trading_weekdays", lambda: {4, 5})

    assert backfill_calendar.is_trading_day(day) is True


def test_explicit_named_holiday_remains_non_trading(monkeypatch) -> None:
    day = date(2026, 8, 20)

    monkeypatch.setattr(backfill_calendar, "_confirmed_market_data_exists", lambda _day: False)
    monkeypatch.setattr(backfill_calendar, "_calendar_status", lambda _day: (False, "Public Holiday"))
    monkeypatch.setattr(backfill_calendar, "_known_non_trading_weekdays", lambda: {4, 5})

    assert backfill_calendar.is_trading_day(day) is False


def test_confirmed_market_data_overrides_stale_false_calendar(monkeypatch) -> None:
    day = date(2026, 8, 20)

    monkeypatch.setattr(backfill_calendar, "_confirmed_market_data_exists", lambda _day: True)
    monkeypatch.setattr(backfill_calendar, "_calendar_status", lambda _day: (False, None))

    assert backfill_calendar.is_trading_day(day) is True


def test_history_window_keeps_small_gap_fetches_bounded() -> None:
    years = _history_years_for_window(date(2026, 7, 31), date(2026, 8, 20))
    assert 0.10 <= years < 0.20


def test_eod_refresh_waits_for_post_close_settle_buffer() -> None:
    before_buffer = datetime(2026, 8, 20, 15, 10, tzinfo=NPT_OFFSET)
    after_buffer = datetime(2026, 8, 20, 15, 16, tzinfo=NPT_OFFSET)

    assert _should_refresh_eod(before_buffer) is False
    assert _should_refresh_eod(after_buffer) is True
