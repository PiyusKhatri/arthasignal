from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.dependencies import get_current_user
from src.database.connection import get_session
from src.database.models import Company, PriceAlert, PriceAlertCondition, SignalAlert, User
from src.pipeline.extract_signal_calls import TARGET_SIGNAL_HORIZONS

router = APIRouter(prefix="/alerts", tags=["alerts"])


class PriceAlertCreate(BaseModel):
    symbol: str
    condition: PriceAlertCondition
    target_price: Decimal


class PriceAlertResponse(BaseModel):
    id: int
    symbol: str
    condition: PriceAlertCondition
    target_price: Decimal
    is_active: bool
    triggered_at: datetime | None
    created_at: datetime


class SignalAlertCreate(BaseModel):
    symbol: str | None = None
    signal_name: str


class SignalAlertResponse(BaseModel):
    id: int
    symbol: str | None
    signal_name: str
    is_active: bool
    triggered_at: datetime | None
    triggered_symbol: str | None
    created_at: datetime


class TriggeredAlert(BaseModel):
    alert_type: str
    id: int
    symbol: str | None
    description: str
    triggered_at: datetime


def _to_price_response(alert: PriceAlert) -> PriceAlertResponse:
    return PriceAlertResponse(
        id=alert.id,
        symbol=alert.symbol,
        condition=alert.condition,
        target_price=alert.target_price,
        is_active=alert.is_active,
        triggered_at=alert.triggered_at,
        created_at=alert.created_at,
    )


def _to_signal_response(alert: SignalAlert) -> SignalAlertResponse:
    return SignalAlertResponse(
        id=alert.id,
        symbol=alert.symbol,
        signal_name=alert.signal_name,
        is_active=alert.is_active,
        triggered_at=alert.triggered_at,
        triggered_symbol=alert.triggered_symbol,
        created_at=alert.created_at,
    )


def _load_owned_price_alert(session: Session, alert_id: int, user_id: int) -> PriceAlert:
    alert = session.execute(
        select(PriceAlert).where(PriceAlert.id == alert_id).where(PriceAlert.user_id == user_id)
    ).scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Price alert not found")
    return alert


def _load_owned_signal_alert(session: Session, alert_id: int, user_id: int) -> SignalAlert:
    alert = session.execute(
        select(SignalAlert).where(SignalAlert.id == alert_id).where(SignalAlert.user_id == user_id)
    ).scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal alert not found")
    return alert


@router.post("/price", response_model=PriceAlertResponse, status_code=status.HTTP_201_CREATED)
def create_price_alert(payload: PriceAlertCreate, current_user: User = Depends(get_current_user)) -> PriceAlertResponse:
    symbol = payload.symbol.strip().upper()

    with get_session() as session:
        company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
        if company is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown symbol: {symbol}")

        alert = PriceAlert(
            user_id=current_user.id,
            symbol=symbol,
            condition=payload.condition,
            target_price=payload.target_price,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(alert)
        session.flush()
        session.expunge(alert)

    return _to_price_response(alert)


@router.get("/price", response_model=list[PriceAlertResponse])
def list_price_alerts(current_user: User = Depends(get_current_user)) -> list[PriceAlertResponse]:
    with get_session() as session:
        alerts = session.execute(
            select(PriceAlert).where(PriceAlert.user_id == current_user.id).order_by(PriceAlert.created_at.desc())
        ).scalars().all()
        session.expunge_all()

    return [_to_price_response(a) for a in alerts]


@router.delete("/price/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_price_alert(alert_id: int, current_user: User = Depends(get_current_user)) -> None:
    with get_session() as session:
        alert = _load_owned_price_alert(session, alert_id, current_user.id)
        session.delete(alert)


@router.post("/signal", response_model=SignalAlertResponse, status_code=status.HTTP_201_CREATED)
def create_signal_alert(payload: SignalAlertCreate, current_user: User = Depends(get_current_user)) -> SignalAlertResponse:
    if payload.signal_name not in TARGET_SIGNAL_HORIZONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown signal: {payload.signal_name}")

    symbol = payload.symbol.strip().upper() if payload.symbol else None

    with get_session() as session:
        if symbol is not None:
            company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
            if company is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown symbol: {symbol}")

        alert = SignalAlert(
            user_id=current_user.id,
            symbol=symbol,
            signal_name=payload.signal_name,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(alert)
        session.flush()
        session.expunge(alert)

    return _to_signal_response(alert)


@router.get("/signal", response_model=list[SignalAlertResponse])
def list_signal_alerts(current_user: User = Depends(get_current_user)) -> list[SignalAlertResponse]:
    with get_session() as session:
        alerts = session.execute(
            select(SignalAlert).where(SignalAlert.user_id == current_user.id).order_by(SignalAlert.created_at.desc())
        ).scalars().all()
        session.expunge_all()

    return [_to_signal_response(a) for a in alerts]


@router.delete("/signal/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_signal_alert(alert_id: int, current_user: User = Depends(get_current_user)) -> None:
    with get_session() as session:
        alert = _load_owned_signal_alert(session, alert_id, current_user.id)
        session.delete(alert)


@router.get("/triggered", response_model=list[TriggeredAlert])
def list_triggered_alerts(current_user: User = Depends(get_current_user)) -> list[TriggeredAlert]:
    with get_session() as session:
        price_alerts = session.execute(
            select(PriceAlert)
            .where(PriceAlert.user_id == current_user.id)
            .where(PriceAlert.triggered_at.is_not(None))
        ).scalars().all()
        signal_alerts = session.execute(
            select(SignalAlert)
            .where(SignalAlert.user_id == current_user.id)
            .where(SignalAlert.triggered_at.is_not(None))
        ).scalars().all()
        session.expunge_all()

    results = [
        TriggeredAlert(
            alert_type="price",
            id=a.id,
            symbol=a.symbol,
            description=f"{a.symbol} {a.condition.value} {a.target_price}",
            triggered_at=a.triggered_at,
        )
        for a in price_alerts
    ]
    results += [
        TriggeredAlert(
            alert_type="signal",
            id=a.id,
            symbol=a.triggered_symbol or a.symbol,
            description=f"{a.signal_name}" + (f" on {a.triggered_symbol}" if a.triggered_symbol else ""),
            triggered_at=a.triggered_at,
        )
        for a in signal_alerts
    ]
    results.sort(key=lambda r: r.triggered_at, reverse=True)
    return results
