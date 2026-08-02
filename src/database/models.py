from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ActionType(str, enum.Enum):
    BONUS = "bonus"
    RIGHT = "right"
    DIVIDEND = "dividend"
    SPLIT = "split"


class SymbolHistoryEventType(str, enum.Enum):
    MERGER = "merger"
    NAME_CHANGE = "name_change"


class SignalTimeframe(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class SignalCallStatus(str, enum.Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    VOID = "void"


class SignalCallOutcome(str, enum.Enum):
    WIN = "win"
    LOSS = "loss"
    VOID = "void"


class SignalConfidenceTier(str, enum.Enum):
    HIGH_CONFIDENCE = "high_confidence"
    WEAK_OR_NO_EDGE = "weak_or_no_edge"
    UNRELIABLE_LOW_SAMPLE = "unreliable_low_sample"
    INCONSISTENT_ACROSS_HORIZONS = "inconsistent_across_horizons"
    DECAYED_EDGE = "decayed_edge"
    LIQUIDITY_INVERTED = "liquidity_inverted"
    UNSTABLE_MULTI_DIMENSIONAL = "unstable_multi_dimensional"


class ConfluenceConfidenceTier(str, enum.Enum):
    CONSISTENT_HIGH_CONFIDENCE = "consistent_high_confidence"
    CONSISTENT_LOW_SAMPLE = "consistent_low_sample"
    INCONSISTENT = "inconsistent"


class Company(Base):
    __tablename__ = "companies"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instrument_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    listed_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    daily_prices: Mapped[list["DailyPrice"]] = relationship(back_populates="company")
    corporate_actions: Mapped[list["CorporateAction"]] = relationship(back_populates="company")
    fundamentals: Mapped[list["Fundamental"]] = relationship(back_populates="company")
    signal_calls: Mapped[list["SignalCall"]] = relationship(back_populates="company")


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_daily_prices_symbol_date"),
        Index("ix_daily_prices_symbol", "symbol"),
        Index("ix_daily_prices_date", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    turnover: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    adjusted_close: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)

    company: Mapped["Company"] = relationship(back_populates="daily_prices")


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("symbol", "action_date", "action_type", name="uq_corporate_actions_symbol_date_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    action_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType, name="action_type_enum"), nullable=False)
    ratio_or_amount: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    fiscal_year: Mapped[str] = mapped_column(String(20), nullable=False)

    company: Mapped["Company"] = relationship(back_populates="corporate_actions")


class Fundamental(Base):
    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("symbol", "reported_date", name="uq_fundamentals_symbol_reported_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    fiscal_year: Mapped[str] = mapped_column(String(20), nullable=False)
    eps: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pb_ratio: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    book_value: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    market_capitalization: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    reported_date: Mapped[date] = mapped_column(Date, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="fundamentals")


class MarketIndex(Base):
    __tablename__ = "market_index"
    __table_args__ = (
        UniqueConstraint("index_name", "date", name="uq_market_index_name_date"),
        Index("ix_market_index_date", "date"),
        Index("ix_market_index_name", "index_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    index_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    points_change: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    percent_change: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(nullable=False)
    holiday_name: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SymbolHistory(Base):
    __tablename__ = "symbol_history"
    __table_args__ = (
        UniqueConstraint("old_symbol", "new_symbol", "event_type", name="uq_symbol_history_old_new_type"),
        Index("ix_symbol_history_old_symbol", "old_symbol"),
        Index("ix_symbol_history_new_symbol", "new_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    old_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    new_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[SymbolHistoryEventType] = mapped_column(
        Enum(SymbolHistoryEventType, name="symbol_history_event_type_enum"), nullable=False
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class TechnicalSignal(Base):
    __tablename__ = "technical_signals"
    __table_args__ = (
        UniqueConstraint("symbol", "date", "timeframe", name="uq_technical_signals_symbol_date_timeframe"),
        Index("ix_technical_signals_symbol", "symbol"),
        Index("ix_technical_signals_date", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[SignalTimeframe] = mapped_column(
        Enum(SignalTimeframe, name="signal_timeframe_enum"), nullable=False
    )

    sma_20: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    sma_50: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    sma_100: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    sma_200: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    ema_20: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    ema_50: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    rsi_14: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    macd_line: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    macd_signal: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    macd_histogram: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    stochastic_k: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    stochastic_d: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    cci_20: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    roc_12: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    bollinger_middle: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    bollinger_upper: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    bollinger_lower: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    atr_14: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    obv: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    vwap_20: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fifty_two_week_high: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fifty_two_week_low: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot_r1: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot_r2: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot_r3: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot_s1: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot_s2: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pivot_s3: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_0: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_236: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_382: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_50: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_618: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_786: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    fib_100: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)

    doji: Mapped[bool | None] = mapped_column(nullable=True)
    marubozu_bullish: Mapped[bool | None] = mapped_column(nullable=True)
    marubozu_bearish: Mapped[bool | None] = mapped_column(nullable=True)
    hammer: Mapped[bool | None] = mapped_column(nullable=True)
    shooting_star: Mapped[bool | None] = mapped_column(nullable=True)
    spinning_top: Mapped[bool | None] = mapped_column(nullable=True)
    bullish_engulfing: Mapped[bool | None] = mapped_column(nullable=True)
    bearish_engulfing: Mapped[bool | None] = mapped_column(nullable=True)
    bullish_harami: Mapped[bool | None] = mapped_column(nullable=True)
    bearish_harami: Mapped[bool | None] = mapped_column(nullable=True)
    piercing_line: Mapped[bool | None] = mapped_column(nullable=True)
    dark_cloud_cover: Mapped[bool | None] = mapped_column(nullable=True)
    tweezer_top: Mapped[bool | None] = mapped_column(nullable=True)
    tweezer_bottom: Mapped[bool | None] = mapped_column(nullable=True)
    morning_star: Mapped[bool | None] = mapped_column(nullable=True)
    evening_star: Mapped[bool | None] = mapped_column(nullable=True)
    three_white_soldiers: Mapped[bool | None] = mapped_column(nullable=True)
    three_black_crows: Mapped[bool | None] = mapped_column(nullable=True)


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    __table_args__ = (
        UniqueConstraint("signal_name", "forward_days", name="uq_backtest_results_signal_forward_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_return: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    median_return: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    std_dev: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SignalConfidence(Base):
    __tablename__ = "signal_confidence"
    __table_args__ = (UniqueConstraint("signal_name", name="uq_signal_confidence_signal_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[SignalConfidenceTier] = mapped_column(
        Enum(SignalConfidenceTier, name="signal_confidence_tier_enum"), nullable=False
    )
    avg_win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    min_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    universe_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    liquidity_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    mtf_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    recommended_holding_period: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_viability_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class SignalRegimeStability(Base):
    __tablename__ = "signal_regime_stability"
    __table_args__ = (
        UniqueConstraint("signal_name", "period", name="uq_signal_regime_stability_signal_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class ConfluenceBacktestResult(Base):
    __tablename__ = "confluence_backtest_results"
    __table_args__ = (
        UniqueConstraint("signal_a", "signal_b", "forward_days", name="uq_confluence_backtest_results_pair_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_a: Mapped[str] = mapped_column(String(100), nullable=False)
    signal_b: Mapped[str] = mapped_column(String(100), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    mean_return: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ConfluenceConfidence(Base):
    __tablename__ = "confluence_confidence"
    __table_args__ = (UniqueConstraint("signal_a", "signal_b", name="uq_confluence_confidence_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_a: Mapped[str] = mapped_column(String(100), nullable=False)
    signal_b: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[ConfluenceConfidenceTier] = mapped_column(
        Enum(ConfluenceConfidenceTier, name="confluence_confidence_tier_enum"), nullable=False
    )
    avg_win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    min_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class VolumeConfirmedBacktestResult(Base):
    __tablename__ = "volume_confirmed_backtest_results"
    __table_args__ = (
        UniqueConstraint(
            "pattern_name", "volume_condition", "forward_days", name="uq_volume_confirmed_pattern_condition_days"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pattern_name: Mapped[str] = mapped_column(String(100), nullable=False)
    volume_condition: Mapped[str] = mapped_column(String(20), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class VolumeConditionalTier(Base):
    __tablename__ = "volume_conditional_tier"
    __table_args__ = (
        UniqueConstraint("pattern_name", "volume_condition", name="uq_volume_conditional_tier_pattern_condition"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pattern_name: Mapped[str] = mapped_column(String(100), nullable=False)
    volume_condition: Mapped[str] = mapped_column(String(20), nullable=False)
    tier: Mapped[SignalConfidenceTier] = mapped_column(
        Enum(SignalConfidenceTier, name="signal_confidence_tier_enum"), nullable=False
    )
    avg_win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    min_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class SymbolLiquidityTier(Base):
    __tablename__ = "symbol_liquidity_tier"
    __table_args__ = (UniqueConstraint("symbol", name="uq_symbol_liquidity_tier_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    avg_daily_turnover: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    liquidity_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LiquidityStratifiedBacktestResult(Base):
    __tablename__ = "liquidity_stratified_backtest_results"
    __table_args__ = (
        UniqueConstraint(
            "signal_name", "liquidity_tier", "forward_days", name="uq_liquidity_stratified_signal_tier_days"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    liquidity_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MtfAgreementBacktestResult(Base):
    __tablename__ = "mtf_agreement_backtest_results"
    __table_args__ = (
        UniqueConstraint(
            "signal_name", "agreement_group", "forward_days", name="uq_mtf_agreement_signal_group_days"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agreement_group: Mapped[str] = mapped_column(String(20), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    win_rate_minus_baseline: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TransactionCostAdjustedReturn(Base):
    __tablename__ = "transaction_cost_adjusted_returns"
    __table_args__ = (
        UniqueConstraint(
            "signal_name", "forward_days", "cost_scenario", name="uq_tx_cost_signal_days_scenario"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_scenario: Mapped[str] = mapped_column(String(30), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_return: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    round_trip_cost_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    adjusted_mean_return: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    remains_positive: Mapped[bool | None] = mapped_column(nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SystemNote(Base):
    __tablename__ = "system_notes"
    __table_args__ = (UniqueConstraint("note_key", name="uq_system_notes_note_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_key: Mapped[str] = mapped_column(String(100), nullable=False)
    note_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class IntradaySnapshot(Base):
    __tablename__ = "intraday_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "snapshot_time", name="uq_intraday_snapshots_symbol_time"),
        Index("ix_intraday_snapshots_symbol", "symbol"),
        Index("ix_intraday_snapshots_snapshot_time", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ltp: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    volume_so_far: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turnover_so_far: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    day_high_so_far: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    day_low_so_far: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    percent_change: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class IntradayIndexSnapshot(Base):
    __tablename__ = "intraday_index_snapshots"
    __table_args__ = (
        UniqueConstraint("index_name", "snapshot_time", name="uq_intraday_index_snapshots_name_time"),
        Index("ix_intraday_index_snapshots_index_name", "index_name"),
        Index("ix_intraday_index_snapshots_snapshot_time", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    index_name: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_value: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    percent_change: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    points_change: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)


class IntradayFloorsheet(Base):
    __tablename__ = "intraday_floorsheet"
    __table_args__ = (
        UniqueConstraint("symbol", "contract_no", name="uq_intraday_floorsheet_symbol_contract_no"),
        Index("ix_intraday_floorsheet_symbol", "symbol"),
        Index("ix_intraday_floorsheet_snapshot_time", "snapshot_time"),
        Index("ix_intraday_floorsheet_buyer_broker_id", "buyer_broker_id"),
        Index("ix_intraday_floorsheet_seller_broker_id", "seller_broker_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    contract_no: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    buyer_broker_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    seller_broker_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contract_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contract_rate: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    contract_amount: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)


class SectorIndexMapping(Base):
    __tablename__ = "sector_index_mapping"
    __table_args__ = (UniqueConstraint("companies_sector", name="uq_sector_index_mapping_companies_sector"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    companies_sector: Mapped[str] = mapped_column(String(100), nullable=False)
    market_index_name: Mapped[str] = mapped_column(String(100), nullable=False)


class Broker(Base):
    __tablename__ = "brokers"

    broker_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    broker_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False)


class PromoterHolding(Base):
    __tablename__ = "promoter_holding"
    __table_args__ = (UniqueConstraint("symbol", "reported_date", name="uq_promoter_holding_symbol_reported_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    total_shares: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    promoter_shares: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    promoter_pct: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    public_shares: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    public_pct: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    lock_status: Mapped[str] = mapped_column(String(20), nullable=False)
    prom_lock_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    mf_lock_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reported_date: Mapped[date] = mapped_column(Date, nullable=False)


class ShortTermInterestRate(Base):
    __tablename__ = "short_term_interest_rates"
    __table_args__ = (UniqueConstraint("fiscal_year", "month", name="uq_short_term_interest_rates_fy_month"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fiscal_year: Mapped[str] = mapped_column(String(20), nullable=False)
    month: Mapped[str] = mapped_column(String(20), nullable=False)
    treasury_bill_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    interbank_commercial_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    interbank_other_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class Remittance(Base):
    __tablename__ = "remittance"
    __table_args__ = (UniqueConstraint("fiscal_year", "month", name="uq_remittance_fy_month"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fiscal_year: Mapped[str] = mapped_column(String(20), nullable=False)
    month: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_billions: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    growth_pct: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class GdpNepse(Base):
    __tablename__ = "gdp_nepse"
    __table_args__ = (UniqueConstraint("fiscal_year", name="uq_gdp_nepse_fiscal_year"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fiscal_year: Mapped[str] = mapped_column(String(20), nullable=False)
    gdp_growth_rate: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    market_cap_gdp_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    nepse_index: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)


class IpoCalendar(Base):
    __tablename__ = "ipo_calendar"
    __table_args__ = (
        UniqueConstraint("company_name", "issue_type", "opening_date", name="uq_ipo_calendar_company_type_opening"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    closing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    units_offered: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    min_application_units: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result_announced_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SectorFundamentalBaseline(Base):
    __tablename__ = "sector_fundamental_baseline"
    __table_args__ = (UniqueConstraint("sector", name="uq_sector_fundamental_baseline_sector"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    avg_pe: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    avg_pb: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    excluded_outlier_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MarketPulseBacktestResult(Base):
    __tablename__ = "market_pulse_backtest_results"
    __table_args__ = (
        UniqueConstraint(
            "metric_name", "condition", "forward_days", name="uq_market_pulse_backtest_metric_condition_days"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    condition: Mapped[str] = mapped_column(String(100), nullable=False)
    forward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_forward_return: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    mean_forward_return_minus_baseline: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    p_value: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    is_significant: Mapped[bool | None] = mapped_column(nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SignalCall(Base):
    __tablename__ = "signal_calls"
    __table_args__ = (
        UniqueConstraint("symbol", "signal_name", "entry_date", name="uq_signal_calls_symbol_signal_entry_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    signal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    resolution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolution_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    status: Mapped[SignalCallStatus] = mapped_column(
        Enum(SignalCallStatus, name="signal_call_status_enum"), nullable=False
    )
    outcome: Mapped[SignalCallOutcome | None] = mapped_column(
        Enum(SignalCallOutcome, name="signal_call_outcome_enum"), nullable=True
    )
    forward_days_horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_logic_commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="signal_calls")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "purchase_date", name="uq_holdings_user_symbol_purchase_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), ForeignKey("companies.symbol"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    purchase_price: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
