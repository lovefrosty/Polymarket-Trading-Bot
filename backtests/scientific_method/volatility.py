from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def ewma_lambda(half_life_sec: float, dt_sec: float) -> float:
    if half_life_sec <= 0 or dt_sec <= 0:
        return 0.0
    return math.exp(math.log(0.5) * dt_sec / half_life_sec)


def ewma_update(prev_var: float, r_prev: float, lam: float) -> float:
    return lam * prev_var + (1.0 - lam) * (r_prev**2)


@dataclass
class VolatilityState:
    half_lives: List[float]
    variances: Dict[float, float] = field(default_factory=dict)
    last_ts_ms: Optional[int] = None

    def update(self, r_prev: float, ts_ms: int) -> None:
        if self.last_ts_ms is None:
            for hl in self.half_lives:
                self.variances[hl] = r_prev**2
            self.last_ts_ms = ts_ms
            return
        dt_sec = max(0.001, (ts_ms - self.last_ts_ms) / 1000.0)
        for hl in self.half_lives:
            lam = ewma_lambda(hl, dt_sec)
            prev = self.variances.get(hl, r_prev**2)
            self.variances[hl] = ewma_update(prev, r_prev, lam)
        self.last_ts_ms = ts_ms

    def sigma(self, half_life_sec: float) -> Optional[float]:
        var = self.variances.get(half_life_sec)
        if var is None:
            return None
        return math.sqrt(max(var, 0.0))
