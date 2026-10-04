from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.backtest import event_study as es
from src.collectors import store as text_store
from src.collectors.mentions import build_book
from src.scorecard.daily import NPT
from src.scorecard.grading import build_market
from src.tips import run as tips_run
from src.tips import sources, store
from src.tips.parser import extract

BOOK = build_book([("NABIL", "Nabil Bank Limited"), ("NICA", "NIC Asia Bank Ltd."), ("HRL", "Himalayan Reinsurance Limited")])


@pytest.mark.parametrize("text_value, expected", [
    ("NABIL\nBuy @ 500-510\nTarget 560\nSL 480", [("NABIL", "buy", 500.0, 510.0, 560.0, 480.0)]),
    ("Buy NICA and HRL now!", [("HRL", "buy", None, None, None, None), ("NICA", "buy", None, None, None, None)]),
    ("Should I buy NABIL?", []),
    ("NABIL किन्नुहोस्, लक्ष्य ५६०", [("NABIL", "buy", None, None, 560.0, None)]),
    ("Avoid HRL", [("HRL", "sell", None, None, None, None)]),
    ("NABIL नकिन्नुहोस्", [("NABIL", "sell", None, None, None, None)]),
    ("Don't sell NABIL, hold", []),
    ("Top buyer brokers in NABIL today", []),
    ("NABIL: buy at 500 target 2000 SL 520", [("NABIL", "buy", 500.0, 500.0, None, None)]),
    ("Buy NABIL. Sell NABIL.", []),
])
def test_rule_parser(text_value, expected) -> None:
    got = [(t.symbol, t.direction, t.entry_low, t.entry_high, t.target, t.stop) for t in extract(text_value, BOOK)]
    assert got == expected


def test_signal_date_uses_the_next_open_after_knowledge() -> None:
    sessions = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]
    at = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=NPT)
    assert tips_run.signal_date_for(at(sessions[1], 10, 30), sessions) == sessions[0]
    assert tips_run.signal_date_for(at(sessions[1], 11, 30), sessions) == sessions[1]
    assert tips_run.signal_date_for(at(sessions[2], 14), sessions) == sessions[2]
    assert tips_run.signal_date_for(at(date(2026, 10, 2), 9), sessions) is None
    assert tips_run.signal_date_for(at(date(2026, 9, 27), 9), sessions) is None


def test_manual_entry_refuses_telegram_without_permission_and_needs_url_and_timezone() -> None:
    posted = datetime(2026, 10, 4, 12, tzinfo=NPT)
    with pytest.raises(PermissionError):
        sources.manual_item(BOOK, "telegram", "chan", "https://t.me/chan/1", posted, "Buy NABIL")
    item = sources.manual_item(BOOK, "telegram", "chan", "https://t.me/chan/1", posted, "Buy NABIL", consent_ref="email 2026-10-04")
    assert item.raw["consent_ref"] and item.author is None
    with pytest.raises(ValueError):
        sources.manual_item(BOOK, "youtube", "chan", "not a url", posted, "Buy NABIL")
    with pytest.raises(ValueError):
        sources.manual_item(BOOK, "web", "chan", "https://x.org", datetime(2026, 10, 4, 12), "Buy NABIL")
    assert sources.manual_item(BOOK, "youtube", "c", "https://youtu.be/x", posted, "Buy NABIL").retention == text_store.THIRTY_DAYS


def test_web_pages_respect_robots_and_skip_telegram() -> None:
    def fetch(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return SimpleNamespace(status_code=200, text="User-agent: *\nDisallow: /private\n", headers={})
        return SimpleNamespace(status_code=200, text="<html><title>Tips</title><body>Buy NABIL @ 500</body></html>",
                               headers={"Content-Type": "text/html; charset=utf-8"}, encoding=None)

    sources.WEB_DELAY_SECONDS = 0
    items, detail = sources.web_pages(BOOK, [{"name": "a", "url": "https://x.org/tips"}, {"name": "b", "url": "https://x.org/private/t"},
                                             {"name": "c", "url": "https://t.me/s/chan"}], fetch)
    assert [i.channel for i in items] == ["a"] and items[0].symbols == ["NABIL"] and items[0].author is None
    assert "robots" in detail["b"]["skipped"] and "Telegram" in detail["c"]["skipped"]


SYMBOLS = ["NABIL", "NICA", "HRL"] + [f"S{i:02d}" for i in range(12)]


def _state(sessions):
    rng = np.random.default_rng(1)
    rows = []
    for symbol in SYMBOLS:
        close = 100.0
        for day in sessions:
            prev, close = close, close * float(np.exp(rng.normal(0.0005, 0.01)))
            rows.append((symbol, day, prev, max(prev, close), min(prev, close), close, 1e5, 1e5 * close))
    prices = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
    no_actions = pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])
    panel = es.build_panel(prices, no_actions, {s: "A" for s in SYMBOLS}, sessions=sessions)
    index = prices.groupby("date")["close"].mean().rename("close").reset_index()
    index["open"] = index["close"]
    rates = pd.DataFrame(columns=["fiscal_year", "month", "treasury_bill_rate"])
    return {"panel": panel, "market": build_market(panel, no_actions, index), "inputs": {"index": index, "rates": rates}, "mergers": set()}


@pytest.fixture
def temp_schema():
    from src.database.connection import engine

    name = f"tips_test_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {name}")
    except Exception:
        pytest.skip("database not reachable")
    store.apply_schema(engine, name)
    yield engine, name
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP SCHEMA {name} CASCADE")


def test_tips_flow_from_text_to_ledger_is_append_only_and_deadline_bound(temp_schema) -> None:
    from sqlalchemy import text

    engine, schema = temp_schema
    sessions = []
    day = date(2026, 3, 2)
    while len(sessions) < 260:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    last = sessions[-1]
    state = _state(sessions)
    now = datetime.combine(last, datetime.min.time(), tzinfo=NPT) + timedelta(hours=15)
    weekend = last - timedelta(days=last.weekday() + 1)
    items = [
        sources.manual_item(BOOK, "youtube", "Chan A", "https://youtu.be/a", now - timedelta(hours=1), "NABIL\nBuy @ 100\nTarget 120"),
        sources.manual_item(BOOK, "web", "Chan B", "https://x.org/b", now - timedelta(hours=2), "Avoid HRL"),
        sources.manual_item(BOOK, "web", "Chan B", "https://x.org/c", datetime.combine(weekend, datetime.min.time(), tzinfo=NPT) + timedelta(hours=12),
                            "Buy NICA"),
    ]
    assert text_store.insert_items(engine, items, "test", schema, now=now.astimezone(timezone.utc)) == 3
    extracted = tips_run.extract_all(engine, BOOK, now, schema)
    assert extracted["tips_inserted"] == 3
    assert tips_run.extract_all(engine, BOOK, now, schema)["tips_inserted"] == 0
    counts = tips_run.write_tips(engine, state, set(), now, False, schema)
    assert counts.get("written") == 2 and counts.get("outside_write_window") == 1
    with engine.connect() as connection:
        calls = connection.execute(text(f"SELECT strategy, model_version, symbol FROM {schema}.scorecard_calls ORDER BY 1, 2, 3")).all()
    assert [tuple(c) for c in calls] == [
        ("tip_web_chan_b", "p1-sell", "HRL"), ("tip_youtube_chan_a", "p1-buy", "NABIL"),
        ("tips_all", "p1-buy", "NABIL"), ("tips_all", "p1-sell", "HRL"), ("tips_promoted_avoid", "p1", "NABIL"),
    ]
    assert tips_run.write_tips(engine, state, set(), now, False, schema) == {"pending": 0}
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {schema}.public_tips"))
    pairs = tips_run.tracked_pairs(engine, schema)
    assert {tips_run.side_of(s, v) for s, v in pairs} == {"buy", "avoid"}
    board = tips_run.boards(engine, state, schema)
    assert set(board["aggregate"]) == set(tips_run.HORIZONS) and len(board["channels"][5]) == 2
