from __future__ import annotations

import math
from typing import Iterable, Sequence


def ewma_variance_update(prev_var: float, r: float, dt_sec: float, half_life_sec: float) -> float:
    if half_life_sec <= 0:
        raise ValueError("half_life_sec must be positive")
    if dt_sec <= 0:
        return max(prev_var, 0.0)
    alpha = 1.0 - math.exp(-math.log(2.0) * dt_sec / half_life_sec)
    return (1.0 - alpha) * prev_var + alpha * (r * r)


def ewma_vol(returns: Iterable[float], dt_sec: float, half_life_sec: float) -> float:
    var = 0.0
    for r in returns:
        var = ewma_variance_update(var, r, dt_sec, half_life_sec)
    return math.sqrt(max(var, 0.0))


def percentile_rank(x: float, history: Sequence[float]) -> float:
    if not history:
        return 0.0
    sorted_hist = sorted(history)
    count = len(sorted_hist)
    lo = 0
    hi = count
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_hist[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo / count
