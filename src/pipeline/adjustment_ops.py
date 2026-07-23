from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import select, text

from src.database.connection import get_session
from src.database.models import CorporateAction, DailyPrice
from src.pipeline.adjustment import adjust_price_series

logger = logging.getLogger(__name__)

BULK_UPDATE_ADJUSTED_CLOSE_SQL = text(
    """
    UPDATE daily_prices AS dp
    SET adjusted_close = v.adjusted_close
    FROM (SELECT unnest(:ids) AS id, unnest(:adjusted_closes) AS adjusted_close) AS v
    WHERE dp.id = v.id
    """
)


def symbols_with_corporate_actions() -> list[str]:
    with get_session() as session:
        rows = session.execute(select(CorporateAction.symbol).distinct()).all()
    return [row.symbol for row in rows]


def reapply_adjustment_for_symbol(symbol: str) -> int:
    with get_session() as session:
        price_rows = session.execute(
            select(DailyPrice.id, DailyPrice.date, DailyPrice.close).where(DailyPrice.symbol == symbol)
        ).all()
        action_rows = session.execute(
            select(
                CorporateAction.action_date,
                CorporateAction.action_type,
                CorporateAction.ratio_or_amount,
            ).where(CorporateAction.symbol == symbol)
        ).all()

        if not price_rows or not action_rows:
            return 0

        prices_df = pd.DataFrame(price_rows, columns=["id", "date", "close"])
        prices_df["close"] = prices_df["close"].astype(float)
        actions_df = pd.DataFrame(action_rows, columns=["action_date", "action_type", "ratio_or_amount"])
        actions_df["ratio_or_amount"] = actions_df["ratio_or_amount"].astype(float)

        adjusted_df = adjust_price_series(prices_df, actions_df)

        ids = [int(x) for x in adjusted_df["id"].tolist()]
        adjusted_closes = [float(x) for x in adjusted_df["adjusted_close"].tolist()]

        session.execute(BULK_UPDATE_ADJUSTED_CLOSE_SQL, {"ids": ids, "adjusted_closes": adjusted_closes})
        return len(ids)
