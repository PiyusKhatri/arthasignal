from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.scorecard import spec
from src.scorecard.strategies import Strategy


def truncated_panel(
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    sectors: Mapping[str, str | None],
    sessions: Sequence,
    t: int,
) -> es.Panel:
    cutoff = sessions[t]
    return es.build_panel(
        prices[prices["date"] <= cutoff],
        actions[actions["action_date"] <= cutoff],
        sectors,
        sessions=list(sessions[: t + 1]),
    )


def _same(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    if list(left["symbol"]) != list(right["symbol"]):
        return False
    a = pd.to_numeric(left["score"], errors="coerce").to_numpy(dtype=float)
    b = pd.to_numeric(right["score"], errors="coerce").to_numpy(dtype=float)
    return bool(np.allclose(a, b, equal_nan=True))


def lookahead_audit(
    strategies: Sequence[Strategy],
    full_panel: es.Panel,
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    sectors: Mapping[str, str | None],
    sample: int = spec.AUDIT_SAMPLE_DATES,
    seed: int = spec.AUDIT_SEED,
    first_index: int = 60,
) -> dict[str, dict[str, Any]]:
    sessions = full_panel.sessions
    rng = np.random.default_rng(seed)
    candidates = np.arange(first_index, len(sessions) - 1)
    chosen = sorted(rng.choice(candidates, size=min(sample, len(candidates)), replace=False).tolist())
    mismatches: dict[str, list[str]] = {strategy.name: [] for strategy in strategies}
    for t in chosen:
        truncated = truncated_panel(prices, actions, sectors, sessions, t)
        for strategy in strategies:
            full = strategy.select(full_panel, t).reset_index(drop=True)
            cut = strategy.select(truncated, t).reset_index(drop=True)
            if not _same(full, cut):
                mismatches[strategy.name].append(sessions[t].isoformat())
    return {
        strategy.name: {
            "strategy": strategy.name,
            "version": strategy.version,
            "dates_checked": len(chosen),
            "mismatched_dates": len(mismatches[strategy.name]),
            "first_mismatches": mismatches[strategy.name][:5],
            "leaky": bool(mismatches[strategy.name]),
        }
        for strategy in strategies
    }
