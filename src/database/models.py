from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    Enum,
    ForeignKey,
    Index,
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
