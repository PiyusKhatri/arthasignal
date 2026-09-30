from __future__ import annotations

from datetime import datetime

import src.pipeline.cleanup_intraday_tables as cleanup
from src.database.models import IntradayFloorsheet, IntradayIndexSnapshot, IntradaySnapshot


def test_floorsheet_is_never_in_the_retention_list() -> None:
    assert "intraday_floorsheet" not in cleanup.RETENTION_TABLES
    assert IntradayFloorsheet not in cleanup.RETENTION_TABLES.values()
    assert all(
        getattr(model, "__tablename__", None) != "intraday_floorsheet"
        for model in cleanup.RETENTION_TABLES.values()
    )


def test_only_intraday_snapshot_tables_are_pruned() -> None:
    assert cleanup.RETENTION_TABLES == {
        "intraday_snapshots": IntradaySnapshot,
        "intraday_index_snapshots": IntradayIndexSnapshot,
    }


def test_cleanup_never_issues_a_delete_against_floorsheet(monkeypatch) -> None:
    deleted_models: list[object] = []

    def fake_delete(model: object, cutoff: datetime) -> int:
        deleted_models.append(model)
        return 0

    monkeypatch.setattr(cleanup, "_delete_old_rows", fake_delete)
    summary = cleanup.run_intraday_table_cleanup()

    assert IntradayFloorsheet not in deleted_models
    assert deleted_models == [IntradaySnapshot, IntradayIndexSnapshot]
    assert "intraday_floorsheet" not in summary["deleted_by_table"]
    assert summary["failures"] == 0
