from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.database.holdout_guard import HoldoutQueryViolation
from src.simulation import floorsheet_ohlc as fo
from src.simulation.protocol import load


def write_day(root, day, rows):
    folder = root / f"year={day.year}" / f"month={day.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=["transaction_no", "symbol", "contract_quantity", "contract_rate", "page_number"])
    frame["business_date"] = day.isoformat()
    frame["data_quality_gap_pct"] = 0.5
    path = folder / f"day={day.day:02d}.parquet"
    frame.to_parquet(path, index=False)
    return path


def test_derive_orders_by_contract_and_skips_odd_lots(tmp_path):
    day = date(2017, 5, 2)
    write_day(
        tmp_path,
        day,
        [
            ("201705020000005", "ABC", 50, 105.0, 1),
            ("201705020000002", "ABC", 20, 101.0, 2),
            ("201705020000001", "ABC", 5, 90.0, 3),
            ("201705020000009", "ABC", 10, 103.0, 1),
            ("201705020000003", "XYZ", 100, 50.0, 1),
        ],
    )
    files = fo.floorsheet_files(date(2017, 1, 1), date(2017, 12, 31), tmp_path)
    con = fo.connect(tmp_path / "work")
    board = fo.derive(files, con=con, min_quantity=10).set_index("symbol")
    assert board.loc["ABC", ["open", "high", "low", "last", "trades"]].tolist() == [101.0, 105.0, 101.0, 103.0, 3]
    every = fo.derive(files, con=con).set_index("symbol")
    assert every.loc["ABC", ["open", "low"]].tolist() == [90.0, 90.0]
    assert bool(every.loc["ABC", "prefix_ok"]) and bool(every.loc["ABC", "contracts_unique"])


def test_files_refuse_holdout_and_build_refuses_raw_dir(tmp_path, monkeypatch):
    with pytest.raises(HoldoutQueryViolation):
        fo.floorsheet_files(date(2025, 1, 1), date(2025, 9, 30), tmp_path)
    monkeypatch.setattr(fo, "FLOORSHEET_ROOT", tmp_path)
    with pytest.raises(ValueError):
        fo.build(out_dir=tmp_path / "derived")


def test_decide_needs_every_check():
    rates = {f: {"exact": 0.99, "within_1pct": 0.995} for f in fo.FIELDS}
    weak = dict(rates, low={"exact": 0.91, "within_1pct": 0.95})
    section = {"overall": rates, "by_format": {"15_digit": rates}, "page_drop": {"seed_1": {"overall": rates, "15_digit": weak}}}
    decision = fo.decide(section, load().raw["floorsheet_ohlc"]["acceptance"])
    assert decision["open"]["use"] and decision["high"]["use"]
    assert decision["low"] == {"use": False, "failed": ["15_digit_page_drop_seed_1"]}
