from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models import Base


class BacktestVariantTrial(Base):
    __tablename__ = "backtest_variant_trials"
    __table_args__ = (
        UniqueConstraint("model_family", "variant_fingerprint", name="uq_backtest_variant_family_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_family: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    variant_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BacktestHoldoutEvaluation(Base):
    __tablename__ = "backtest_holdout_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    config_version: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    holdout_start: Mapped[date] = mapped_column(Date, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    call_number: Mapped[int] = mapped_column(Integer, nullable=False)
    variants_tried: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
