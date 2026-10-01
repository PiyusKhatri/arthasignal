from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.backtest import event_study as es

PICKS = 10
MOMENTUM_LOOKBACK = 20
LEAK_HORIZON = 20


def _universe(panel: es.Panel, t: int) -> np.ndarray:
    return np.flatnonzero(~np.isnan(panel.close[:, t]))


def random_picker(panel: es.Panel, t: int, seed: int = 7) -> pd.DataFrame:
    members = _universe(panel, t)
    if not len(members):
        return pd.DataFrame(columns=["symbol", "score"])
    rng = np.random.default_rng([seed, int(panel.sessions[t].toordinal())])
    chosen = rng.choice(members, size=min(PICKS, len(members)), replace=False)
    return pd.DataFrame({"symbol": [panel.symbols[i] for i in chosen], "score": rng.random(len(chosen))})


def equal_weight_universe(panel: es.Panel, t: int) -> pd.DataFrame:
    members = _universe(panel, t)
    return pd.DataFrame({"symbol": [panel.symbols[i] for i in members], "score": np.zeros(len(members))})


def _trailing_return(panel: es.Panel, t: int, lookback: int) -> np.ndarray:
    start = max(0, t - lookback + 1)
    window = panel.total_return[:, start : t + 1]
    with np.errstate(invalid="ignore"):
        return np.prod(1 + np.nan_to_num(window, nan=0.0), axis=1) - 1


def simple_momentum(panel: es.Panel, t: int) -> pd.DataFrame:
    members = _universe(panel, t)
    if t < MOMENTUM_LOOKBACK or not len(members):
        return pd.DataFrame(columns=["symbol", "score"])
    scores = _trailing_return(panel, t, MOMENTUM_LOOKBACK)[members]
    order = np.lexsort((np.array([panel.symbols[i] for i in members]), -scores))[:PICKS]
    return pd.DataFrame({"symbol": [panel.symbols[members[i]] for i in order], "score": scores[order]})


def leaky_future(panel: es.Panel, t: int) -> pd.DataFrame:
    members = _universe(panel, t)
    end = t + 1 + LEAK_HORIZON
    if not len(members) or end >= len(panel.sessions):
        return pd.DataFrame(columns=["symbol", "score"])
    future = panel.total_return[:, t + 1 : end + 1]
    with np.errstate(invalid="ignore"):
        scores = (np.prod(1 + np.nan_to_num(future, nan=0.0), axis=1) - 1)[members]
    if np.all(scores == 0):
        return pd.DataFrame(columns=["symbol", "score"])
    order = np.lexsort((np.array([panel.symbols[i] for i in members]), -scores))[:PICKS]
    return pd.DataFrame({"symbol": [panel.symbols[members[i]] for i in order], "score": scores[order]})


@dataclass(frozen=True)
class Strategy:
    name: str
    version: str
    probability: float
    select: Callable[[es.Panel, int], pd.DataFrame]
    parameters: dict[str, Any] = field(default_factory=dict)


STRATEGIES = (
    Strategy("baseline_random", "v1", 0.5, random_picker, {"picks": PICKS, "seed": 7}),
    Strategy("baseline_equal_weight", "v1", 0.5, equal_weight_universe, {}),
    Strategy("baseline_momentum", "v1", 0.5, simple_momentum, {"picks": PICKS, "lookback": MOMENTUM_LOOKBACK}),
    Strategy("leaky_future_return", "v1", 0.5, leaky_future, {"picks": PICKS, "leak_horizon": LEAK_HORIZON}),
)
