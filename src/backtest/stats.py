from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import NormalDist, mean, stdev
from typing import Hashable, Mapping, Sequence

MIN_CLUSTERS = 2


@dataclass(frozen=True)
class ClusteredInterval:
    mean: float
    low: float
    high: float
    clusters: int
    observations: int
    alpha: float


def adjusted_alpha(base_alpha: float, variants_tried: int) -> float:
    return base_alpha / max(1, variants_tried)


def clustered_mean_interval(
    values_by_cluster: Mapping[Hashable, Sequence[float]],
    alpha: float = 0.05,
) -> ClusteredInterval | None:
    cluster_means = [mean(values) for values in values_by_cluster.values() if values]
    if len(cluster_means) < MIN_CLUSTERS:
        return None
    center = mean(cluster_means)
    standard_error = stdev(cluster_means) / sqrt(len(cluster_means))
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    return ClusteredInterval(
        mean=center,
        low=center - z * standard_error,
        high=center + z * standard_error,
        clusters=len(cluster_means),
        observations=sum(len(values) for values in values_by_cluster.values()),
        alpha=alpha,
    )
