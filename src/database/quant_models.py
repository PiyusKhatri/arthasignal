from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models import Base


class QuantShadowSignal(Base):
    """Point-in-time ledger used to validate probabilistic ArthaSignal research calls.

    Rows are immutable at capture except for resolution fields. The ledger is
    deliberately separate from the legacy indicator signal-call protocol so
    calibration of the new probability model can be audited independently.
    """

    __tablename__ = "quant_shadow_signals"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "as_of_date",
            "horizon_days",
            "feature_version",
            name="uq_quant_shadow_symbol_date_horizon_version",
        ),
        Index("ix_quant_shadow_status", "status"),
        Index("ix_quant_shadow_as_of_date", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_version: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    probability_outperform: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_excess_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    entry_price: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    market_entry: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resolution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolution_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    market_resolution: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    realized_stock_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    realized_market_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    realized_excess_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    success_after_cost: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class QuantModelSnapshot(Base):
    """Immutable trained model artifact plus untouched historical test metrics."""

    __tablename__ = "quant_model_snapshots"
    __table_args__ = (
        UniqueConstraint("model_version", "trained_through", name="uq_quant_model_version_date"),
        Index("ix_quant_model_snapshot_trained_through", "trained_through"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(String(60), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(60), nullable=False)
    trained_through: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    training_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    calibration_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    test_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    classifier_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    ranker_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    calibration_intercept: Mapped[float | None] = mapped_column(Numeric(14, 8), nullable=True)
    calibration_slope: Mapped[float | None] = mapped_column(Numeric(14, 8), nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class QuantRobustnessRun(Base):
    """Immutable robustness audit linked to one exact trained model snapshot."""

    __tablename__ = "quant_robustness_runs"
    __table_args__ = (
        UniqueConstraint("model_snapshot_id", "policy_version", name="uq_quant_robustness_snapshot_policy"),
        Index("ix_quant_robustness_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("quant_model_snapshots.id"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(60), nullable=False)
    gate_status: Mapped[str] = mapped_column(String(20), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class QuantV41ModelSnapshot(Base):
    """Frozen V4.1 forward-shadow artifact.

    A model_version is intentionally write-once. Retraining requires a new
    version so forward validation cannot silently mix changing parameters.
    """

    __tablename__ = "quant_v41_model_snapshots"
    __table_args__ = (
        UniqueConstraint("model_version", name="uq_quant_v41_model_version"),
        Index("ix_quant_v41_snapshot_trained_through", "trained_through"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(60), nullable=False)
    trained_through: Mapped[date] = mapped_column(Date, nullable=False)
    core_training_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    calibration_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    residual_regressor_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    residual_classifier_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mae_regressor_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mfe_regressor_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    baseline_expectation_json: Mapped[str] = mapped_column(Text, nullable=False)
    residual_calibrator_json: Mapped[str] = mapped_column(Text, nullable=False)
    probability_calibrator_json: Mapped[str] = mapped_column(Text, nullable=False)
    mae_calibrator_json: Mapped[str] = mapped_column(Text, nullable=False)
    mfe_calibrator_json: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class QuantV41ShadowSignal(Base):
    """Immutable V4.1 candidate prediction, mutable only in resolution fields."""

    __tablename__ = "quant_v41_shadow_signals"
    __table_args__ = (
        UniqueConstraint(
            "model_snapshot_id",
            "symbol",
            "as_of_date",
            name="uq_quant_v41_shadow_snapshot_symbol_date",
        ),
        Index("ix_quant_v41_shadow_status", "status"),
        Index("ix_quant_v41_shadow_date", "as_of_date"),
        Index("ix_quant_v41_shadow_selected", "selected_v41", "selected_baseline"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("quant_v41_model_snapshots.id"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    prediction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    baseline_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    v41_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_v41: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override_action: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_score: Mapped[float] = mapped_column(Numeric(14, 8), nullable=False)
    final_score: Mapped[float] = mapped_column(Numeric(14, 8), nullable=False)
    predicted_residual_alpha_percent: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    probability_positive_residual: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    predicted_mae_percent: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    predicted_mfe_percent: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    predicted_payoff_ratio: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    expected_excess_return_percent: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(30), nullable=False)
    sector_regime: Mapped[str] = mapped_column(String(30), nullable=False)
    liquidity_bucket: Mapped[str] = mapped_column(String(20), nullable=False)

    entry_price: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    market_entry: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resolution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolution_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    market_resolution: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    realized_stock_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    realized_market_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    realized_excess_return_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    realized_mae_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    realized_mfe_percent: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    success_after_cost: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class QuantV41ShadowRun(Base):
    """Immutable daily heartbeat proving the frozen V4.1 shadow cycle ran."""

    __tablename__ = "quant_v41_shadow_runs"
    __table_args__ = (
        UniqueConstraint(
            "model_snapshot_id",
            "as_of_date",
            name="uq_quant_v41_shadow_run_snapshot_date",
        ),
        Index("ix_quant_v41_shadow_run_date", "as_of_date"),
        Index("ix_quant_v41_shadow_run_status", "run_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("quant_v41_model_snapshots.id"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    run_status: Mapped[str] = mapped_column(String(20), nullable=False)
    symbols_considered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    v41_selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_stale_or_untraded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_features: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)
    run_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
