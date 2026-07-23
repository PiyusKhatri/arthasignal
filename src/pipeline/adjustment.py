from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DILUTIVE_ACTION_TYPES = {"bonus", "right"}


def _action_type_value(action_type: object) -> str:
    return action_type.value if hasattr(action_type, "value") else str(action_type)


def _compute_action_factor(action_type: str, ratio_or_amount: float) -> float:
    if action_type in DILUTIVE_ACTION_TYPES:
        if ratio_or_amount <= 0:
            logger.warning("Non-positive ratio_or_amount %s for %s action, skipping adjustment", ratio_or_amount, action_type)
            return 1.0
        return 100.0 / (100.0 + ratio_or_amount)
    if action_type == "split":
        if ratio_or_amount <= 0:
            logger.warning("Non-positive ratio_or_amount %s for split action, skipping adjustment", ratio_or_amount)
            return 1.0
        return 1.0 / ratio_or_amount
    logger.info("Action type %s does not affect price adjustment, skipping", action_type)
    return 1.0


def adjust_price_series(prices_df: pd.DataFrame, corporate_actions_df: pd.DataFrame) -> pd.DataFrame:
    result = prices_df.copy()
    result["date"] = pd.to_datetime(result["date"])

    if corporate_actions_df is None or corporate_actions_df.empty:
        result["adjusted_close"] = result["close"]
        return result

    actions = corporate_actions_df.copy()
    actions["action_date"] = pd.to_datetime(actions["action_date"])
    actions["action_type"] = actions["action_type"].map(_action_type_value)
    actions = actions.sort_values("action_date").reset_index(drop=True)

    factors = np.array(
        [
            _compute_action_factor(row.action_type, row.ratio_or_amount)
            for row in actions.itertuples(index=False)
        ]
    )

    suffix_product = np.ones(len(factors) + 1)
    for i in range(len(factors) - 1, -1, -1):
        suffix_product[i] = suffix_product[i + 1] * factors[i]

    action_dates = actions["action_date"].to_numpy()
    row_dates = result["date"].to_numpy()
    indices = np.searchsorted(action_dates, row_dates, side="right")
    multipliers = suffix_product[indices]

    result["adjusted_close"] = result["close"] * multipliers
    return result
