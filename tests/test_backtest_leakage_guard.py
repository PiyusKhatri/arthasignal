from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from src.backtest.config import load_holdout_config
from src.backtest.costs import COST_LEVELS_ROUND_TRIP
from src.backtest.holdout import (
    HoldoutViolation,
    assert_development_only,
    development_only,
    final_evaluation,
    partition_rows,
)
from src.backtest.ledger import STATUS_COMPLETED, STATUS_FAILED, DatabaseLedger, InMemoryLedger
from src.backtest.models import BacktestHoldoutEvaluation
from src.backtest.splits import fold_rows, walk_forward_folds
from src.backtest.stats import adjusted_alpha, clustered_mean_interval
from src.backtest.types import EXIT_ON_TIME, Label, Row
from src.backtest.universe import survivorship_coverage
from src.database.connection import engine

CONFIG = load_holdout_config()
HOLDOUT_START = CONFIG.holdout_start


def _row(symbol: str, signal_date: date, exit_offset_days: int = 30, gross: float = 0.04) -> Row:
    entry_date = signal_date + timedelta(days=1)
    return Row(
        symbol=symbol,
        signal_date=signal_date,
        features={"return_20d": 1.0},
        label=Label(
            entry_date=entry_date,
            entry_price=100.0,
            exit_date=signal_date + timedelta(days=exit_offset_days),
            exit_price=100.0 * (1.0 + gross),
            gross_return=gross,
            exit_status=EXIT_ON_TIME,
        ),
        benchmark_return=0.01,
        momentum_score=1.0 if symbol == "AAA" else 0.5,
    )


def _sample_rows() -> list[Row]:
    rows: list[Row] = []
    for offset in (400, 300, 200):
        rows.append(_row("AAA", HOLDOUT_START - timedelta(days=offset)))
    rows.append(_row("EDGE", HOLDOUT_START - timedelta(days=10)))
    for offset in (0, 7, 14):
        day = HOLDOUT_START + timedelta(days=offset)
        rows.append(_row("AAA", day, gross=0.05))
        rows.append(_row("BBB", day, gross=-0.02))
    return rows


def test_holdout_config_is_locked_to_the_last_twelve_months_and_everything_after() -> None:
    assert CONFIG.version == "2026-09-30-holdout-v1"
    assert CONFIG.holdout_start == date(2025, 9, 30)
    assert CONFIG.holdout_end is None
    assert CONFIG.horizon_sessions == 20
    assert CONFIG.cost_levels_round_trip == COST_LEVELS_ROUND_TRIP == (0.005, 0.010, 0.015)
    assert len(CONFIG.sha256) == 64


@pytest.mark.parametrize("purpose", ["training", "feature_selection", "tuning", "walk_forward"])
def test_development_code_cannot_receive_holdout_labels(purpose: str) -> None:
    with pytest.raises(HoldoutViolation):
        assert_development_only(_sample_rows(), CONFIG, purpose)


def test_label_that_resolves_inside_the_holdout_is_a_violation() -> None:
    boundary = _row("EDGE", HOLDOUT_START - timedelta(days=10))
    assert boundary.signal_date < HOLDOUT_START <= boundary.label.exit_date
    with pytest.raises(HoldoutViolation):
        assert_development_only([boundary], CONFIG, "training")


def test_development_only_decorator_blocks_the_wrapped_function() -> None:
    calls: list[int] = []

    @development_only("tuning", CONFIG)
    def tune(rows: list[Row]) -> int:
        calls.append(len(rows))
        return len(rows)

    partition = partition_rows(_sample_rows(), CONFIG)
    assert tune(list(partition.development)) == 3
    with pytest.raises(HoldoutViolation):
        tune(_sample_rows())
    assert calls == [3]


def test_partition_seals_holdout_labels_and_drops_boundary_rows() -> None:
    partition = partition_rows(_sample_rows(), CONFIG)
    assert len(partition.development) == 3
    assert partition.boundary_dropped == 1
    assert partition.holdout.count == 6
    assert_development_only(partition.development, CONFIG, "training")

    with pytest.raises(HoldoutViolation):
        partition.holdout.labeled_rows()
    with pytest.raises(HoldoutViolation):
        partition.holdout.labeled_rows(object())
    with pytest.raises(HoldoutViolation):
        list(partition.holdout)
    with pytest.raises(HoldoutViolation):
        len(partition.holdout)
    with pytest.raises(HoldoutViolation):
        partition.holdout[0]

    feature_rows = partition.holdout.feature_rows()
    assert len(feature_rows) == 6
    assert all(row.label is None and row.benchmark_return is None for row in feature_rows)
    assert_development_only(feature_rows, CONFIG, "feature_selection")


def test_final_evaluation_logs_every_call_before_reading_labels() -> None:
    partition = partition_rows(_sample_rows(), CONFIG)
    ledger = InMemoryLedger()
    ledger.register_variant("demo", {"depth": 3}, "first variant")
    ledger.register_variant("demo", {"depth": 4}, "second variant")
    ledger.register_variant("demo", {"depth": 3}, "repeat of the first variant")
    observed_during_select: list[int] = []

    def select(rows: tuple[Row, ...]) -> set[tuple[str, date]]:
        observed_during_select.append(ledger.holdout_call_count(CONFIG.version))
        assert all(row.label is None for row in rows)
        return {row.key for row in rows if row.symbol == "AAA"}

    first = final_evaluation(
        model_id="demo-model",
        select=select,
        holdout=partition.holdout,
        ledger=ledger,
        requested_by="tester",
        reason="first look",
    )
    second = final_evaluation(
        model_id="demo-model",
        select=select,
        holdout=partition.holdout,
        ledger=ledger,
        requested_by="tester",
        reason="second look",
    )

    assert observed_during_select == [1, 2]
    assert first["holdout_call_number"] == 1
    assert second["holdout_call_number"] == 2
    assert ledger.holdout_call_count(CONFIG.version) == 2
    assert [call["status"] for call in ledger.holdout_calls] == [STATUS_COMPLETED, STATUS_COMPLETED]
    assert first["selected_calls"] == 3
    assert first["active_dates"] == 3
    assert first["variants_tried"] == 2
    assert first["alpha_after_multiple_testing_penalty"] == pytest.approx(0.025)
    assert [level["round_trip_cost"] for level in first["cost_sensitivity"]] == [0.005, 0.010, 0.015]
    one_percent = first["cost_sensitivity"][1]
    assert one_percent["model_net_return"]["mean"] == pytest.approx(0.04)
    assert set(one_percent["baselines"]) == {"nepse_buy_and_hold", "equal_weight_universe", "simple_momentum"}
    assert one_percent["baselines"]["nepse_buy_and_hold"]["model_minus_baseline"]["mean"] == pytest.approx(0.03)
    assert one_percent["baselines"]["equal_weight_universe"]["model_minus_baseline"]["mean"] == pytest.approx(0.035)
    assert one_percent["baselines"]["simple_momentum"]["model_minus_baseline"]["mean"] == pytest.approx(0.0)


def test_failed_final_evaluation_is_still_logged() -> None:
    partition = partition_rows(_sample_rows(), CONFIG)
    ledger = InMemoryLedger()

    def broken_select(rows: tuple[Row, ...]) -> set[tuple[str, date]]:
        raise RuntimeError("model crashed")

    with pytest.raises(RuntimeError):
        final_evaluation(
            model_id="demo-model",
            select=broken_select,
            holdout=partition.holdout,
            ledger=ledger,
            requested_by="tester",
            reason="crash",
        )
    assert ledger.holdout_call_count(CONFIG.version) == 1
    assert ledger.holdout_calls[0]["status"] == STATUS_FAILED


def test_final_evaluation_requires_a_named_requester_and_reason() -> None:
    partition = partition_rows(_sample_rows(), CONFIG)
    ledger = InMemoryLedger()
    with pytest.raises(ValueError):
        final_evaluation(
            model_id="demo-model",
            select=lambda rows: set(),
            holdout=partition.holdout,
            ledger=ledger,
            requested_by="tester",
            reason=" ",
        )
    assert ledger.holdout_call_count(CONFIG.version) == 0


def test_database_ledger_records_holdout_calls_in_a_table() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    @contextmanager
    def scope():
        yield session
        session.flush()

    try:
        ledger = DatabaseLedger(scope)
        before_calls = ledger.holdout_call_count(CONFIG.version)
        before_variants = ledger.variant_count()
        assert ledger.register_variant("pytest-family", {"seed": 1}, "test variant") == before_variants + 1
        assert ledger.register_variant("pytest-family", {"seed": 1}, "same variant") == before_variants + 1

        partition = partition_rows(_sample_rows(), CONFIG)
        report = final_evaluation(
            model_id="pytest-model",
            select=lambda rows: {row.key for row in rows if row.symbol == "AAA"},
            holdout=partition.holdout,
            ledger=ledger,
            requested_by="pytest",
            reason="ledger round trip",
        )
        assert report["holdout_call_number"] == before_calls + 1
        assert ledger.holdout_call_count(CONFIG.version) == before_calls + 1
        stored = session.query(BacktestHoldoutEvaluation).filter_by(model_id="pytest-model").one()
        assert stored.status == STATUS_COMPLETED
        assert stored.call_number == before_calls + 1
        assert stored.variants_tried == before_variants + 1
        assert stored.config_sha256 == CONFIG.sha256
        assert stored.summary_json is not None
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _sessions(count: int, end: date) -> list[date]:
    return [end - timedelta(days=offset) for offset in range(count, 0, -1)]


def test_walk_forward_folds_purge_embargo_and_never_touch_the_holdout() -> None:
    sessions = _sessions(700, HOLDOUT_START) + [HOLDOUT_START + timedelta(days=offset) for offset in range(50)]
    folds = walk_forward_folds(sessions, CONFIG, n_folds=4, min_train_sessions=252)
    development = sorted(session for session in sessions if session < HOLDOUT_START)

    assert len(folds) == 4
    assert folds[0].gap_sessions == CONFIG.horizon_sessions + CONFIG.grace_sessions + 1 + CONFIG.embargo_sessions == 29
    seen_test: set[date] = set()
    for fold in folds:
        assert max(fold.test_sessions) < HOLDOUT_START
        assert len(fold.train_sessions) >= 252
        gap = development.index(fold.test_sessions[0]) - development.index(fold.train_sessions[-1]) - 1
        assert gap == fold.gap_sessions
        assert not seen_test & set(fold.test_sessions)
        seen_test |= set(fold.test_sessions)
    assert len(folds[1].train_sessions) > len(folds[0].train_sessions)


def test_fold_rows_purges_training_labels_that_resolve_inside_the_test_window() -> None:
    sessions = _sessions(700, HOLDOUT_START)
    fold = walk_forward_folds(sessions, CONFIG, n_folds=4, min_train_sessions=252)[0]
    test_start = fold.test_sessions[0]
    clean = _row("AAA", fold.train_sessions[0], exit_offset_days=25)
    late = _row("BBB", fold.train_sessions[-1], exit_offset_days=(test_start - fold.train_sessions[-1]).days + 2)
    tested = _row("CCC", test_start, exit_offset_days=25)

    train, test = fold_rows([clean, late, tested], fold, CONFIG)
    assert train == [clean]
    assert test == [tested]

    with pytest.raises(HoldoutViolation):
        fold_rows([clean, _row("DDD", HOLDOUT_START)], fold, CONFIG)


def test_clustered_interval_uses_dates_not_individual_calls() -> None:
    correlated = {date(2025, 1, 1): [0.10] * 50, date(2025, 1, 2): [-0.10] * 50}
    interval = clustered_mean_interval(correlated, alpha=0.05)
    assert interval is not None
    assert interval.clusters == 2
    assert interval.observations == 100
    assert interval.mean == pytest.approx(0.0)
    assert interval.low < -0.10 and interval.high > 0.10
    assert clustered_mean_interval({date(2025, 1, 1): [0.1, 0.2]}) is None


def test_multiple_testing_penalty_widens_the_interval() -> None:
    values = {date(2025, 1, day): [0.01 * day] for day in range(1, 11)}
    single = clustered_mean_interval(values, adjusted_alpha(0.05, 1))
    many = clustered_mean_interval(values, adjusted_alpha(0.05, 20))
    assert adjusted_alpha(0.05, 0) == 0.05
    assert adjusted_alpha(0.05, 20) == pytest.approx(0.0025)
    assert many.high - many.low > single.high - single.low


def test_survivorship_coverage_reports_delisted_price_coverage() -> None:
    companies = [("AAA", "A"), ("BBB", "A"), ("OLD1", "D"), ("OLD2", "D"), ("OLD3", "D"), ("HALT", "S")]
    report = survivorship_coverage(companies, {"AAA", "BBB", "OLD1"})
    assert report["by_status"]["D"] == {"listed": 3, "with_prices": 1}
    assert report["active_price_coverage"] == 1.0
    assert report["delisted_price_coverage"] == pytest.approx(1 / 3)
    assert report["suspended_price_coverage"] == 0.0
    assert report["non_active_listed"] == 4
    assert report["non_active_price_coverage"] == pytest.approx(0.25)
