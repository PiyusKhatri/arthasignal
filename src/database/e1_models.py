from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models import Base


class QuantE1ForwardRun(Base):
    """Append-only E1 forward decision heartbeat tied to frozen V4.1 evidence."""

    __tablename__ = "quant_e1_forward_runs"
    __table_args__ = (
        Index("ix_quant_e1_forward_run_date", "as_of_date"),
        Index("ix_quant_e1_forward_run_status", "run_status"),
        Index("ix_quant_e1_forward_snapshot_date", "model_snapshot_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("quant_v41_model_snapshots.id"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    predictive_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    execution_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    artifact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_v41_run_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    run_status: Mapped[str] = mapped_column(String(20), nullable=False)
    candidate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)
    run_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class QuantE1ForwardDecision(Base):
    """Immutable E1 candidate snapshot used for deterministic forward replay."""

    __tablename__ = "quant_e1_forward_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "symbol", name="uq_quant_e1_forward_run_symbol"),
        Index("ix_quant_e1_forward_decision_date", "as_of_date"),
        Index("ix_quant_e1_forward_decision_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("quant_e1_forward_runs.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_prediction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_percentile: Mapped[float] = mapped_column(Numeric(14, 8), nullable=False)
    baseline_score: Mapped[float] = mapped_column(Numeric(14, 8), nullable=False)
    final_score: Mapped[float] = mapped_column(Numeric(14, 8), nullable=False)
    override_action: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_excess_return_percent: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(30), nullable=False)
    sector_regime: Mapped[str] = mapped_column(String(30), nullable=False)
    liquidity_bucket: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_eligible_v41: Mapped[bool] = mapped_column(Boolean, nullable=False)
    hold_eligible_v41: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_eligible_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decision_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
