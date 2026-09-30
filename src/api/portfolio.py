from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.dependencies import get_current_user
from src.database.connection import get_session
from src.database.models import Company, DailyPrice, Holding, User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if len(symbol) > 20:
        raise ValueError("symbol must not exceed 20 characters")
    return symbol


def _validate_purchase_date(value: date | None) -> date | None:
    if value is not None and value > date.today():
        raise ValueError("purchase_date cannot be in the future")
    return value


class HoldingCreate(BaseModel):
    symbol: str
    quantity: Decimal = Field(gt=0)
    purchase_price: Decimal = Field(gt=0)
    purchase_date: date

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)

    @field_validator("purchase_date")
    @classmethod
    def validate_purchase_date(cls, value: date) -> date:
        return _validate_purchase_date(value)  # type: ignore[return-value]


class HoldingUpdate(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    purchase_price: Decimal | None = Field(default=None, gt=0)
    purchase_date: date | None = None

    @field_validator("purchase_date")
    @classmethod
    def validate_purchase_date(cls, value: date | None) -> date | None:
        return _validate_purchase_date(value)


class HoldingResponse(BaseModel):
    id: int
    symbol: str
    quantity: Decimal
    purchase_price: Decimal
    purchase_date: date
    created_at: datetime


class HoldingSummary(BaseModel):
    id: int
    symbol: str
    quantity: Decimal
    purchase_price: Decimal
    purchase_date: date
    current_price: Decimal | None
    invested_amount: Decimal
    current_value: Decimal | None
    unrealized_pl: Decimal | None
    unrealized_pl_percent: Decimal | None


class PortfolioSummary(BaseModel):
    total_invested: Decimal
    total_current_value: Decimal | None
    total_unrealized_pl: Decimal | None
    total_unrealized_pl_percent: Decimal | None
    priced_invested_amount: Decimal
    priced_current_value: Decimal
    unpriced_invested_amount: Decimal
    unpriced_holdings_count: int
    valuation_complete: bool
    holdings: list[HoldingSummary]


def _to_response(holding: Holding) -> HoldingResponse:
    return HoldingResponse(
        id=holding.id,
        symbol=holding.symbol,
        quantity=holding.quantity,
        purchase_price=holding.purchase_price,
        purchase_date=holding.purchase_date,
        created_at=holding.created_at,
    )


def _load_owned_holding(session: Session, holding_id: int, user_id: int) -> Holding:
    holding = session.execute(
        select(Holding).where(Holding.id == holding_id).where(Holding.user_id == user_id)
    ).scalar_one_or_none()
    if holding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found")
    return holding


def _load_latest_prices(session: Session, symbols: set[str]) -> dict[str, Decimal]:
    if not symbols:
        return {}

    ranked_prices = (
        select(
            DailyPrice.symbol.label("symbol"),
            DailyPrice.close.label("close"),
            func.row_number()
            .over(partition_by=DailyPrice.symbol, order_by=DailyPrice.date.desc())
            .label("row_num"),
        )
        .where(DailyPrice.symbol.in_(symbols))
        .subquery()
    )
    rows = session.execute(
        select(ranked_prices.c.symbol, ranked_prices.c.close).where(ranked_prices.c.row_num == 1)
    ).all()
    return {row.symbol: Decimal(str(row.close)) for row in rows}


@router.post("/holdings", response_model=HoldingResponse, status_code=status.HTTP_201_CREATED)
def create_holding(payload: HoldingCreate, current_user: User = Depends(get_current_user)) -> HoldingResponse:
    with get_session() as session:
        company = session.execute(select(Company).where(Company.symbol == payload.symbol)).scalar_one_or_none()
        if company is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown symbol: {payload.symbol}")

        holding = Holding(
            user_id=current_user.id,
            symbol=payload.symbol,
            quantity=payload.quantity,
            purchase_price=payload.purchase_price,
            purchase_date=payload.purchase_date,
            created_at=datetime.now(timezone.utc),
        )
        session.add(holding)
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A holding for this symbol and purchase date already exists",
            )
        session.expunge(holding)

    return _to_response(holding)


@router.get("/holdings", response_model=list[HoldingResponse])
def list_holdings(current_user: User = Depends(get_current_user)) -> list[HoldingResponse]:
    with get_session() as session:
        holdings = session.execute(
            select(Holding).where(Holding.user_id == current_user.id).order_by(Holding.purchase_date.desc())
        ).scalars().all()
        session.expunge_all()

    return [_to_response(h) for h in holdings]


@router.put("/holdings/{holding_id}", response_model=HoldingResponse)
def update_holding(
    holding_id: int, payload: HoldingUpdate, current_user: User = Depends(get_current_user)
) -> HoldingResponse:
    with get_session() as session:
        holding = _load_owned_holding(session, holding_id, current_user.id)

        if payload.quantity is not None:
            holding.quantity = payload.quantity
        if payload.purchase_price is not None:
            holding.purchase_price = payload.purchase_price
        if payload.purchase_date is not None:
            holding.purchase_date = payload.purchase_date

        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A holding for this symbol and purchase date already exists",
            )
        session.expunge(holding)

    return _to_response(holding)


@router.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(holding_id: int, current_user: User = Depends(get_current_user)) -> None:
    with get_session() as session:
        holding = _load_owned_holding(session, holding_id, current_user.id)
        session.delete(holding)


@router.get("/summary", response_model=PortfolioSummary)
def get_portfolio_summary(current_user: User = Depends(get_current_user)) -> PortfolioSummary:
    with get_session() as session:
        holdings = session.execute(select(Holding).where(Holding.user_id == current_user.id)).scalars().all()
        symbols = {h.symbol for h in holdings}
        latest_prices = _load_latest_prices(session, symbols)
        session.expunge_all()

    holding_summaries: list[HoldingSummary] = []
    total_invested = Decimal("0")
    priced_invested_amount = Decimal("0")
    priced_current_value = Decimal("0")
    unpriced_invested_amount = Decimal("0")
    unpriced_holdings_count = 0

    for h in holdings:
        quantity = Decimal(str(h.quantity))
        purchase_price = Decimal(str(h.purchase_price))
        invested_amount = quantity * purchase_price
        total_invested += invested_amount

        current_price = latest_prices.get(h.symbol)
        current_value = None
        unrealized_pl = None
        unrealized_pl_percent = None
        if current_price is not None:
            current_value = quantity * current_price
            unrealized_pl = current_value - invested_amount
            unrealized_pl_percent = unrealized_pl / invested_amount * 100
            priced_invested_amount += invested_amount
            priced_current_value += current_value
        else:
            unpriced_invested_amount += invested_amount
            unpriced_holdings_count += 1

        holding_summaries.append(
            HoldingSummary(
                id=h.id,
                symbol=h.symbol,
                quantity=quantity,
                purchase_price=purchase_price,
                purchase_date=h.purchase_date,
                current_price=current_price,
                invested_amount=invested_amount,
                current_value=current_value,
                unrealized_pl=unrealized_pl,
                unrealized_pl_percent=unrealized_pl_percent,
            )
        )

    valuation_complete = unpriced_holdings_count == 0
    total_current_value = priced_current_value if valuation_complete else None
    total_unrealized_pl = (priced_current_value - total_invested) if valuation_complete else None
    total_unrealized_pl_percent = (
        total_unrealized_pl / total_invested * 100
        if valuation_complete and total_unrealized_pl is not None and total_invested != 0
        else None
    )

    return PortfolioSummary(
        total_invested=total_invested,
        total_current_value=total_current_value,
        total_unrealized_pl=total_unrealized_pl,
        total_unrealized_pl_percent=total_unrealized_pl_percent,
        priced_invested_amount=priced_invested_amount,
        priced_current_value=priced_current_value,
        unpriced_invested_amount=unpriced_invested_amount,
        unpriced_holdings_count=unpriced_holdings_count,
        valuation_complete=valuation_complete,
        holdings=holding_summaries,
    )
