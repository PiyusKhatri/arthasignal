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


class SignalConfidenceTier(str, enum.Enum):
    HIGH_CONFIDENCE = "high_confidence"
    WEAK_OR_NO_EDGE = "weak_or_no_edge"
    UNRELIABLE_LOW_SAMPLE = "unreliable_low_sample"
    INCONSISTENT_ACROSS_HORIZONS = "inconsistent_across_horizons"


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
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


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
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


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
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
