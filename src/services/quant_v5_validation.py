"""Pure, immutable historical validation primitives for the V5 challenger."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, median
from typing import Any, Mapping, Sequence

ROUND_TRIP_COST_PERCENT = 1.0
SEVERE_MAE_PERCENT = 5.0
BOOTSTRAP_ITERATIONS = 1_000
BOOTSTRAP_SEED = 20260821
BOOTSTRAP_BLOCK_SESSIONS = 20
STRATEGIES = ("v5", "v41", "baseline")


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = min(1.0, max(0.0, fraction)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class SelectionRecord:
    signal_date: Any
    symbol: str
    fold: Any | None
    gross_excess_return_percent: float | None
    mae_percent: float | None
    success_probability: float | None
    recalibrated_success_probability: float | None
    status: str = "resolved"
    void: bool = False

    @property
    def net_alpha_percent(self) -> float | None:
        if self.gross_excess_return_percent is None:
            return None
        return self.gross_excess_return_percent - ROUND_TRIP_COST_PERCENT

    @property
    def adverse_mae_percent(self) -> float | None:
        if self.mae_percent is None:
            return None
        return abs(min(0.0, self.mae_percent))


@dataclass(frozen=True)
class ExclusionDiagnostic:
    signal_date: Any
    reason: str
    counts: tuple[int, int, int]
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExactBreadthDiagnostics:
    included_dates: tuple[Any, ...]
    exclusions: tuple[ExclusionDiagnostic, ...]
    omissions: tuple[ExclusionDiagnostic, ...] = ()

    @property
    def excluded_dates(self) -> int:
        return len(self.exclusions)


@dataclass(frozen=True)
class MatchedDateMetrics:
    signal_date: Any
    fold: Any | None
    breadth: int
    v5_net_alpha_percent: float
    v41_net_alpha_percent: float
    baseline_net_alpha_percent: float
    v5_minus_v41_percent: float
    v5_minus_baseline_percent: float
    v5_symbols: tuple[str, ...]


@dataclass(frozen=True)
class StrategyMetrics:
    strategy: str
    selected_rows: int
    mean_net_alpha_percent: float | None
    median_net_alpha_percent: float | None


@dataclass(frozen=True)
class PairedMetrics:
    comparator: str
    date_observations: int
    date_mean_incremental_alpha_percent: float | None
    date_median_incremental_alpha_percent: float | None


@dataclass(frozen=True)
class RiskMetrics:
    strategy: str
    samples: int
    severe_drawdown_rate: float | None
    mean_adverse_mae_percent: float | None


@dataclass(frozen=True)
class CalibrationMetrics:
    samples: int
    brier_score: float | None
    climatology_brier_score: float | None
    brier_skill: float | None
    expected_calibration_error: float | None
    missing_probability_rows: int
    missing_climatology_folds: tuple[Any, ...]


@dataclass(frozen=True)
class BootstrapInterval:
    iterations: int
    seed: int
    block_size: int
    units: int
    observations: int
    estimate: float | None
    low: float | None
    high: float | None


@dataclass(frozen=True)
class CohortBootstrap:
    cohort_means: tuple[float, ...]
    interval: BootstrapInterval


@dataclass(frozen=True)
class FoldMetrics:
    fold: Any
    v5_mean_net_alpha_percent: float | None
    v41_mean_net_alpha_percent: float | None
    v5_brier_score: float | None
    recalibrated_v41_brier_score: float | None


@dataclass(frozen=True)
class HistoricalValidationReport:
    diagnostics: ExactBreadthDiagnostics
    matched_dates: tuple[MatchedDateMetrics, ...]
    strategy_metrics: tuple[StrategyMetrics, ...]
    paired_metrics: tuple[PairedMetrics, ...]
    risk_metrics: tuple[RiskMetrics, ...]
    calibration_v5: CalibrationMetrics
    folds: tuple[FoldMetrics, ...]
    date_bootstrap_v41: BootstrapInterval
    date_bootstrap_baseline: BootstrapInterval
    cohort_bootstrap_v41: CohortBootstrap
    cohort_bootstrap_baseline: CohortBootstrap
    consecutive_active_date_jaccards: tuple[float, ...]
    median_consecutive_active_date_jaccard: float | None
    valid_purged_folds: tuple[Any, ...]

    def strategy(self, name: str) -> StrategyMetrics:
        return next(item for item in self.strategy_metrics if item.strategy == name)

    def paired(self, comparator: str) -> PairedMetrics:
        return next(item for item in self.paired_metrics if item.comparator == comparator)

    def risk(self, name: str) -> RiskMetrics:
        return next(item for item in self.risk_metrics if item.strategy == name)


@dataclass(frozen=True)
class ReplayMetrics:
    v5_compounded_return_percent: float | None = None
    v41_compounded_return_percent: float | None = None
    v5_annualized_turnover: float | None = None
    v5_mean_holding_sessions: float | None = None
    v5_max_drawdown_percent: float | None = None
    v41_max_drawdown_percent: float | None = None
    baseline_compounded_return_percent: float | None = None
    baseline_annualized_turnover: float | None = None
    baseline_mean_holding_sessions: float | None = None
    baseline_max_drawdown_percent: float | None = None


@dataclass(frozen=True)
class HistoricalGateMetrics:
    valid_purged_outer_folds: int | None = None
    positive_v5_mean_folds: int | None = None
    v5_beats_v41_folds: int | None = None
    v5_minus_v41_mean_percent: float | None = None
    v5_minus_v41_median_percent: float | None = None
    date_bootstrap_v41_low_percent: float | None = None
    cohort_bootstrap_v41_low_percent: float | None = None
    v5_selected_median_net_alpha_percent: float | None = None
    v5_severe_drawdown_rate: float | None = None
    v41_severe_drawdown_rate: float | None = None
    v5_mean_adverse_mae_percent: float | None = None
    v41_mean_adverse_mae_percent: float | None = None
    v5_brier_skill: float | None = None
    v5_expected_calibration_error: float | None = None
    v5_brier_no_worse_than_v41_folds: int | None = None
    median_consecutive_active_date_jaccard: float | None = None
    v5_replay_compounded_return_percent: float | None = None
    v41_replay_compounded_return_percent: float | None = None
    v5_replay_annualized_turnover: float | None = None
    v5_replay_mean_holding_sessions: float | None = None
    v5_replay_max_drawdown_percent: float | None = None
    v41_replay_max_drawdown_percent: float | None = None
    selected_v5_rows: int | None = None
    active_dates: int | None = None
    baseline_comparison_complete: bool | None = None


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool


@dataclass(frozen=True)
class HistoricalGateEvaluation:
    status: str
    checks: tuple[GateCheck, ...]
    missing_metrics: tuple[str, ...]

    def check(self, name: str) -> bool:
        return next(item.passed for item in self.checks if item.name == name)


def _record(row: SelectionRecord | Mapping[str, Any]) -> SelectionRecord:
    if isinstance(row, SelectionRecord):
        return row
    return SelectionRecord(
        signal_date=row.get("signal_date", row.get("as_of_date", row.get("date"))),
        symbol=str(row.get("symbol") or ""),
        fold=row.get("fold", row.get("fold_id")),
        gross_excess_return_percent=_finite(
            row.get("gross_excess_return_percent", row.get("excess_return_percent"))
        ),
        mae_percent=_finite(row.get("mae_percent", row.get("realized_mae_percent"))),
        success_probability=_finite(row.get("success_probability")),
        recalibrated_success_probability=_finite(row.get("recalibrated_success_probability")),
        status=str(row.get("status", "resolved")),
        void=bool(row.get("void", False)) or row.get("status") == "void",
    )


def _group(rows: Sequence[SelectionRecord | Mapping[str, Any]]) -> dict[Any, tuple[SelectionRecord, ...]]:
    grouped: dict[Any, list[SelectionRecord]] = defaultdict(list)
    for value in rows:
        row = _record(value)
        grouped[row.signal_date].append(row)
    return {key: tuple(value) for key, value in grouped.items()}


def _invalid_details(rows: Sequence[SelectionRecord]) -> tuple[str, ...]:
    details: list[str] = []
    if len({row.symbol for row in rows}) != len(rows):
        details.append("duplicate_symbol")
    if any(row.status != "resolved" for row in rows):
        details.append("unresolved_selection")
    if any(row.void for row in rows):
        details.append("void_selection")
    if any(row.gross_excess_return_percent is None for row in rows):
        details.append("missing_gross_excess_return")
    if any(row.mae_percent is None for row in rows):
        details.append("missing_mae")
    return tuple(details)


def _match_dates(
    grouped: Mapping[str, Mapping[Any, tuple[SelectionRecord, ...]]],
    sessions: tuple[Any, ...],
) -> tuple[tuple[MatchedDateMetrics, ...], ExactBreadthDiagnostics, dict[str, tuple[SelectionRecord, ...]]]:
    matches: list[MatchedDateMetrics] = []
    exclusions: list[ExclusionDiagnostic] = []
    omissions: list[ExclusionDiagnostic] = []
    selected: dict[str, list[SelectionRecord]] = {name: [] for name in STRATEGIES}
    all_dates = set().union(*(set(grouped[name]) for name in STRATEGIES))
    session_set = set(sessions)
    for signal_date in sorted(all_dates):
        original_rows = tuple(grouped[name].get(signal_date, ()) for name in STRATEGIES)
        original_counts = tuple(len(value) for value in original_rows)
        if signal_date not in session_set:
            exclusions.append(ExclusionDiagnostic(signal_date, "date_not_in_market_calendar", original_counts))
            continue
        invalid_symbols = {
            row.symbol
            for strategy_rows in original_rows
            for row in strategy_rows
            if row.status != "resolved" or row.void
            or row.gross_excess_return_percent is None or row.mae_percent is None
        }
        rows = tuple(
            tuple(row for row in strategy_rows if row.symbol not in invalid_symbols)
            for strategy_rows in original_rows
        )
        counts = tuple(len(value) for value in rows)
        if invalid_symbols:
            omissions.append(ExclusionDiagnostic(
                signal_date,
                "symmetric_invalid_row_omission",
                tuple(original - retained for original, retained in zip(original_counts, counts)),
                tuple(sorted(invalid_symbols)),
            ))
        if any(count == 0 for count in counts):
            exclusions.append(ExclusionDiagnostic(signal_date, "missing_strategy_selection", counts))
            continue
        if len(set(counts)) != 1:
            exclusions.append(ExclusionDiagnostic(signal_date, "breadth_mismatch", counts))
            continue
        details = tuple(detail for strategy_rows in rows for detail in _invalid_details(strategy_rows))
        folds = {row.fold for strategy_rows in rows for row in strategy_rows if row.fold is not None}
        if len(folds) > 1:
            details = (*details, "fold_mismatch")
        if details:
            exclusions.append(ExclusionDiagnostic(signal_date, "invalid_matched_outcome", counts, details))
            continue
        date_means = tuple(mean(row.net_alpha_percent for row in strategy_rows) for strategy_rows in rows)  # type: ignore[arg-type]
        matches.append(
            MatchedDateMetrics(
                signal_date=signal_date,
                fold=next(iter(folds), None),
                breadth=counts[0],
                v5_net_alpha_percent=date_means[0],
                v41_net_alpha_percent=date_means[1],
                baseline_net_alpha_percent=date_means[2],
                v5_minus_v41_percent=date_means[0] - date_means[1],
                v5_minus_baseline_percent=date_means[0] - date_means[2],
                v5_symbols=tuple(sorted(row.symbol for row in rows[0])),
            )
        )
        for name, strategy_rows in zip(STRATEGIES, rows):
            selected[name].extend(strategy_rows)
    diagnostics = ExactBreadthDiagnostics(
        tuple(row.signal_date for row in matches), tuple(exclusions), tuple(omissions)
    )
    return tuple(matches), diagnostics, {name: tuple(rows) for name, rows in selected.items()}


def _bootstrap_iid(values: Sequence[float], *, iterations: int, seed: int, block_size: int) -> BootstrapInterval:
    if not values:
        return BootstrapInterval(0, seed, block_size, 0, 0, None, None, None)
    rng = random.Random(seed)
    samples = [mean(values[rng.randrange(len(values))] for _ in values) for _ in range(iterations)]
    return BootstrapInterval(
        iterations, seed, block_size, len(values), len(values), mean(values),
        _percentile(samples, 0.025), _percentile(samples, 0.975),
    )


def moving_block_bootstrap(
    session_values: Sequence[float | None],
    *,
    block_size: int = BOOTSTRAP_BLOCK_SESSIONS,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> BootstrapInterval:
    """Bootstrap contiguous market-session blocks after date aggregation."""
    values = tuple(_finite(value) for value in session_values)
    observed = tuple(value for value in values if value is not None)
    if not observed:
        return BootstrapInterval(0, seed, block_size, len(values), 0, None, None, None)
    if block_size < 1 or iterations < 1:
        raise ValueError("block_size and iterations must be positive")
    width = min(block_size, len(values))
    starts = range(len(values) - width + 1)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        sampled: list[float | None] = []
        while len(sampled) < len(values):
            start = rng.choice(starts)
            sampled.extend(values[start : start + width])
        present = [value for value in sampled[: len(values)] if value is not None]
        if present:
            samples.append(mean(present))
    return BootstrapInterval(
        len(samples), seed, block_size, len(values), len(observed), mean(observed),
        _percentile(samples, 0.025), _percentile(samples, 0.975),
    )


def non_overlapping_cohort_bootstrap(
    session_values: Sequence[float | None],
    *,
    cohort_size: int = BOOTSTRAP_BLOCK_SESSIONS,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> CohortBootstrap:
    """Aggregate complete, non-overlapping market-session cohorts before resampling."""
    if cohort_size < 1:
        raise ValueError("cohort_size must be positive")
    values = tuple(_finite(value) for value in session_values)
    cohorts: list[float] = []
    for start in range(0, len(values) - cohort_size + 1, cohort_size):
        present = [value for value in values[start : start + cohort_size] if value is not None]
        if present:
            cohorts.append(mean(present))
    return CohortBootstrap(tuple(cohorts), _bootstrap_iid(cohorts, iterations=iterations, seed=seed, block_size=cohort_size))


def _strategy_metrics(name: str, rows: Sequence[SelectionRecord]) -> StrategyMetrics:
    values = [row.net_alpha_percent for row in rows if row.net_alpha_percent is not None]
    return StrategyMetrics(name, len(values), mean(values) if values else None, median(values) if values else None)


def _risk_metrics(name: str, rows: Sequence[SelectionRecord]) -> RiskMetrics:
    values = [row.adverse_mae_percent for row in rows if row.adverse_mae_percent is not None]
    return RiskMetrics(
        name, len(values),
        mean(1.0 if value >= SEVERE_MAE_PERCENT else 0.0 for value in values) if values else None,
        mean(values) if values else None,
    )


def _calibration(
    rows: Sequence[SelectionRecord],
    climatology: Mapping[Any, float],
    *,
    recalibrated: bool = False,
) -> CalibrationMetrics:
    probabilities = [
        row.recalibrated_success_probability if recalibrated else row.success_probability for row in rows
    ]
    missing_probabilities = sum(value is None or not 0.0 <= value <= 1.0 for value in probabilities)
    missing_folds = tuple(sorted({row.fold for row in rows if _finite(climatology.get(row.fold)) is None}))
    if not rows or missing_probabilities or missing_folds:
        return CalibrationMetrics(len(rows), None, None, None, None, missing_probabilities, missing_folds)
    outcomes = [1.0 if (row.net_alpha_percent or 0.0) > 0.0 else 0.0 for row in rows]
    probs = [float(value) for value in probabilities if value is not None]
    climates = [float(climatology[row.fold]) for row in rows]
    if any(not 0.0 <= value <= 1.0 for value in climates):
        return CalibrationMetrics(len(rows), None, None, None, None, 0, tuple(sorted({row.fold for row in rows})))
    brier = mean((probability - outcome) ** 2 for probability, outcome in zip(probs, outcomes))
    climate_brier = mean((probability - outcome) ** 2 for probability, outcome in zip(climates, outcomes))
    skill = 1.0 - brier / climate_brier if climate_brier > 0.0 else None
    ece = 0.0
    for index in range(10):
        members = [
            position for position, probability in enumerate(probs)
            if index / 10.0 <= probability < (index + 1) / 10.0 or (index == 9 and probability == 1.0)
        ]
        if members:
            ece += len(members) / len(probs) * abs(
                mean(probs[position] for position in members) - mean(outcomes[position] for position in members)
            )
    return CalibrationMetrics(len(rows), brier, climate_brier, skill, ece, 0, ())


def _fold_metrics(
    matches: Sequence[MatchedDateMetrics],
    selected: Mapping[str, tuple[SelectionRecord, ...]],
    climatology: Mapping[Any, float],
) -> tuple[FoldMetrics, ...]:
    result: list[FoldMetrics] = []
    for fold in sorted({row.fold for row in matches if row.fold is not None}):
        v5_rows = [row for row in selected["v5"] if row.fold == fold]
        v41_rows = [row for row in selected["v41"] if row.fold == fold]
        v5_net = [row.net_alpha_percent for row in v5_rows if row.net_alpha_percent is not None]
        v41_net = [row.net_alpha_percent for row in v41_rows if row.net_alpha_percent is not None]
        v5_calibration = _calibration(v5_rows, climatology)
        v41_calibration = _calibration(v41_rows, climatology, recalibrated=True)
        result.append(FoldMetrics(
            fold,
            mean(v5_net) if v5_net else None,
            mean(v41_net) if v41_net else None,
            v5_calibration.brier_score,
            v41_calibration.brier_score,
        ))
    return tuple(result)


def build_historical_validation_report(
    *,
    v5_rows: Sequence[SelectionRecord | Mapping[str, Any]],
    v41_rows: Sequence[SelectionRecord | Mapping[str, Any]],
    baseline_rows: Sequence[SelectionRecord | Mapping[str, Any]],
    market_sessions: Sequence[Any],
    training_climatology_by_fold: Mapping[Any, float],
    valid_purged_folds: Sequence[Any],
) -> HistoricalValidationReport:
    """Build exact-date/exact-breadth statistics without mutating caller data."""
    sessions = tuple(market_sessions)
    if not sessions or len(set(sessions)) != len(sessions) or tuple(sorted(sessions)) != sessions:
        raise ValueError("market_sessions must be a non-empty, unique, ordered sequence")
    grouped = {"v5": _group(v5_rows), "v41": _group(v41_rows), "baseline": _group(baseline_rows)}
    matches, diagnostics, selected = _match_dates(grouped, sessions)
    by_date = {row.signal_date: row for row in matches}
    v41_values = tuple(by_date[day].v5_minus_v41_percent if day in by_date else None for day in sessions)
    baseline_values = tuple(by_date[day].v5_minus_baseline_percent if day in by_date else None for day in sessions)
    jaccards: list[float] = []
    for previous, current in zip(matches, matches[1:]):
        left, right = set(previous.v5_symbols), set(current.v5_symbols)
        jaccards.append(len(left & right) / len(left | right))
    return HistoricalValidationReport(
        diagnostics=diagnostics,
        matched_dates=matches,
        strategy_metrics=tuple(_strategy_metrics(name, selected[name]) for name in STRATEGIES),
        paired_metrics=(
            PairedMetrics("v41", len(matches), mean([row.v5_minus_v41_percent for row in matches]) if matches else None, median([row.v5_minus_v41_percent for row in matches]) if matches else None),
            PairedMetrics("baseline", len(matches), mean([row.v5_minus_baseline_percent for row in matches]) if matches else None, median([row.v5_minus_baseline_percent for row in matches]) if matches else None),
        ),
        risk_metrics=tuple(_risk_metrics(name, selected[name]) for name in STRATEGIES),
        calibration_v5=_calibration(selected["v5"], training_climatology_by_fold),
        folds=_fold_metrics(matches, selected, training_climatology_by_fold),
        date_bootstrap_v41=moving_block_bootstrap(v41_values),
        date_bootstrap_baseline=moving_block_bootstrap(baseline_values, seed=BOOTSTRAP_SEED + 1),
        cohort_bootstrap_v41=non_overlapping_cohort_bootstrap(v41_values),
        cohort_bootstrap_baseline=non_overlapping_cohort_bootstrap(baseline_values, seed=BOOTSTRAP_SEED + 1),
        consecutive_active_date_jaccards=tuple(jaccards),
        median_consecutive_active_date_jaccard=median(jaccards) if jaccards else None,
        valid_purged_folds=tuple(valid_purged_folds),
    )


def derive_historical_gate_metrics(report: HistoricalValidationReport, replay: ReplayMetrics) -> HistoricalGateMetrics:
    v5 = report.strategy("v5")
    v5_risk, v41_risk = report.risk("v5"), report.risk("v41")
    pair = report.paired("v41")
    comparable_brier_folds = [
        fold for fold in report.folds
        if fold.v5_brier_score is not None and fold.recalibrated_v41_brier_score is not None
    ]
    baseline_pair = report.paired("baseline")
    valid_folds = set(report.valid_purged_folds)
    metric_folds = {fold.fold for fold in report.folds}
    valid_fold_count = len(valid_folds) if valid_folds == metric_folds else None
    baseline_exclusions = any(
        exclusion.counts[2] != exclusion.counts[1]
        for exclusion in report.diagnostics.exclusions
        if exclusion.counts[0] > 0 or exclusion.counts[1] > 0
    )
    baseline_replay = (
        replay.baseline_compounded_return_percent,
        replay.baseline_annualized_turnover,
        replay.baseline_mean_holding_sessions,
        replay.baseline_max_drawdown_percent,
    )
    baseline_complete = (
        bool(report.matched_dates)
        and baseline_pair.date_observations == len(report.matched_dates)
        and report.risk("baseline").samples == report.strategy("baseline").selected_rows
        and report.date_bootstrap_baseline.low is not None
        and report.cohort_bootstrap_baseline.interval.low is not None
        and not baseline_exclusions
        and all(value is not None for value in baseline_replay)
    )
    return HistoricalGateMetrics(
        valid_purged_outer_folds=valid_fold_count,
        positive_v5_mean_folds=sum((fold.v5_mean_net_alpha_percent or 0.0) > 0.0 for fold in report.folds),
        v5_beats_v41_folds=sum(
            fold.v5_mean_net_alpha_percent is not None and fold.v41_mean_net_alpha_percent is not None
            and fold.v5_mean_net_alpha_percent > fold.v41_mean_net_alpha_percent for fold in report.folds
        ),
        v5_minus_v41_mean_percent=pair.date_mean_incremental_alpha_percent,
        v5_minus_v41_median_percent=pair.date_median_incremental_alpha_percent,
        date_bootstrap_v41_low_percent=report.date_bootstrap_v41.low,
        cohort_bootstrap_v41_low_percent=report.cohort_bootstrap_v41.interval.low,
        v5_selected_median_net_alpha_percent=v5.median_net_alpha_percent,
        v5_severe_drawdown_rate=v5_risk.severe_drawdown_rate,
        v41_severe_drawdown_rate=v41_risk.severe_drawdown_rate,
        v5_mean_adverse_mae_percent=v5_risk.mean_adverse_mae_percent,
        v41_mean_adverse_mae_percent=v41_risk.mean_adverse_mae_percent,
        v5_brier_skill=report.calibration_v5.brier_skill,
        v5_expected_calibration_error=report.calibration_v5.expected_calibration_error,
        v5_brier_no_worse_than_v41_folds=sum(
            fold.v5_brier_score <= fold.recalibrated_v41_brier_score for fold in comparable_brier_folds  # type: ignore[operator]
        ) if len(comparable_brier_folds) == len(report.valid_purged_folds) else None,
        median_consecutive_active_date_jaccard=report.median_consecutive_active_date_jaccard,
        v5_replay_compounded_return_percent=replay.v5_compounded_return_percent,
        v41_replay_compounded_return_percent=replay.v41_compounded_return_percent,
        v5_replay_annualized_turnover=replay.v5_annualized_turnover,
        v5_replay_mean_holding_sessions=replay.v5_mean_holding_sessions,
        v5_replay_max_drawdown_percent=replay.v5_max_drawdown_percent,
        v41_replay_max_drawdown_percent=replay.v41_max_drawdown_percent,
        selected_v5_rows=v5.selected_rows,
        active_dates=len(report.matched_dates),
        baseline_comparison_complete=baseline_complete,
    )


def evaluate_historical_gate(metrics: HistoricalGateMetrics) -> HistoricalGateEvaluation:
    """Evaluate every frozen historical hurdle; absent values always fail."""
    required = tuple(name for name in HistoricalGateMetrics.__dataclass_fields__ if getattr(metrics, name) is None)
    checks = (
        GateCheck("exactly_four_valid_purged_outer_folds", metrics.valid_purged_outer_folds == 4),
        GateCheck("positive_v5_mean_in_three_folds", metrics.positive_v5_mean_folds is not None and 3 <= metrics.positive_v5_mean_folds <= 4),
        GateCheck("v5_beats_v41_in_three_folds", metrics.v5_beats_v41_folds is not None and 3 <= metrics.v5_beats_v41_folds <= 4),
        GateCheck("v5_minus_v41_mean_positive", metrics.v5_minus_v41_mean_percent is not None and metrics.v5_minus_v41_mean_percent > 0.0),
        GateCheck("v5_minus_v41_median_positive", metrics.v5_minus_v41_median_percent is not None and metrics.v5_minus_v41_median_percent > 0.0),
        GateCheck("date_bootstrap_lower_bound_positive", metrics.date_bootstrap_v41_low_percent is not None and metrics.date_bootstrap_v41_low_percent > 0.0),
        GateCheck("cohort_bootstrap_lower_bound_positive", metrics.cohort_bootstrap_v41_low_percent is not None and metrics.cohort_bootstrap_v41_low_percent > 0.0),
        GateCheck("selected_v5_median_net_alpha_positive", metrics.v5_selected_median_net_alpha_percent is not None and metrics.v5_selected_median_net_alpha_percent > 0.0),
        GateCheck("severe_drawdown_rate_no_worse_than_v41", metrics.v5_severe_drawdown_rate is not None and metrics.v41_severe_drawdown_rate is not None and metrics.v5_severe_drawdown_rate <= metrics.v41_severe_drawdown_rate),
        GateCheck("mean_mae_no_worse_than_v41", metrics.v5_mean_adverse_mae_percent is not None and metrics.v41_mean_adverse_mae_percent is not None and metrics.v5_mean_adverse_mae_percent <= metrics.v41_mean_adverse_mae_percent),
        GateCheck("brier_skill_positive", metrics.v5_brier_skill is not None and metrics.v5_brier_skill > 0.0),
        GateCheck("ece_at_most_005", metrics.v5_expected_calibration_error is not None and metrics.v5_expected_calibration_error <= 0.05),
        GateCheck("brier_no_worse_than_v41_in_three_folds", metrics.v5_brier_no_worse_than_v41_folds is not None and 3 <= metrics.v5_brier_no_worse_than_v41_folds <= 4),
        GateCheck("median_jaccard_at_least_050", metrics.median_consecutive_active_date_jaccard is not None and metrics.median_consecutive_active_date_jaccard >= 0.50),
        GateCheck("v5_replay_compounded_return_positive", metrics.v5_replay_compounded_return_percent is not None and metrics.v5_replay_compounded_return_percent > 0.0),
        GateCheck("v5_replay_beats_v41", metrics.v5_replay_compounded_return_percent is not None and metrics.v41_replay_compounded_return_percent is not None and metrics.v5_replay_compounded_return_percent > metrics.v41_replay_compounded_return_percent),
        GateCheck("v5_replay_turnover_at_most_30", metrics.v5_replay_annualized_turnover is not None and metrics.v5_replay_annualized_turnover <= 30.0),
        GateCheck("v5_replay_mean_holding_at_least_5", metrics.v5_replay_mean_holding_sessions is not None and metrics.v5_replay_mean_holding_sessions >= 5.0),
        GateCheck("v5_replay_drawdown_no_worse_than_v41", metrics.v5_replay_max_drawdown_percent is not None and metrics.v41_replay_max_drawdown_percent is not None and metrics.v5_replay_max_drawdown_percent >= metrics.v41_replay_max_drawdown_percent),
        GateCheck("at_least_100_selected_v5_rows", metrics.selected_v5_rows is not None and metrics.selected_v5_rows >= 100),
        GateCheck("at_least_50_active_dates", metrics.active_dates is not None and metrics.active_dates >= 50),
        GateCheck("baseline_comparison_complete", metrics.baseline_comparison_complete is True),
    )
    passed = not required and all(check.passed for check in checks)
    return HistoricalGateEvaluation("pass" if passed else "research_rejected", checks, required)


__all__ = [
    "BOOTSTRAP_BLOCK_SESSIONS", "BOOTSTRAP_ITERATIONS", "BOOTSTRAP_SEED",
    "BootstrapInterval", "CalibrationMetrics", "CohortBootstrap", "ExactBreadthDiagnostics",
    "HistoricalGateEvaluation", "HistoricalGateMetrics", "HistoricalValidationReport",
    "ReplayMetrics", "ROUND_TRIP_COST_PERCENT", "SelectionRecord",
    "build_historical_validation_report", "derive_historical_gate_metrics",
    "evaluate_historical_gate", "moving_block_bootstrap", "non_overlapping_cohort_bootstrap",
]
