from __future__ import annotations

from typing import Sequence

COST_LEVELS_ROUND_TRIP: tuple[float, ...] = (0.005, 0.010, 0.015)


def net_return(gross_return: float, round_trip_cost: float) -> float:
    return gross_return - round_trip_cost


def cost_sensitivity(
    gross_returns: Sequence[float],
    cost_levels: Sequence[float] = COST_LEVELS_ROUND_TRIP,
) -> dict[float, float | None]:
    if not gross_returns:
        return {level: None for level in cost_levels}
    mean_gross = sum(gross_returns) / len(gross_returns)
    return {level: net_return(mean_gross, level) for level in cost_levels}
