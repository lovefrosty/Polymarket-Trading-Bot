from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional

from core.volatility import ewma_variance_update


@dataclass
class DpdsState:
    last_ts_ms: Optional[int] = None
    last_logit: Optional[float] = None
    last_spot: Optional[float] = None
    cov: float = 0.0
    var: float = 0.0
    samples: int = 0


@dataclass(frozen=True)
class DpdsResult:
    dpds: Optional[float]
    beta_logit: Optional[float]
    blockers: List[str]


class DpdsEstimator:
    def __init__(
        self,
        half_life_sec: float = 300.0,
        min_samples: int = 5,
        var_floor: float = 1e-8,
    ) -> None:
        self.half_life_sec = half_life_sec
        self.min_samples = min_samples
        self.var_floor = var_floor
        self._states: Dict[str, DpdsState] = {}

    def estimate_dpds(
        self,
        asset_id: str,
        p_market: Optional[float],
        spot_mid: Optional[float],
        now_ts_ms: int,
    ) -> DpdsResult:
        blockers: List[str] = []
        if p_market is None or spot_mid is None:
            return DpdsResult(None, None, ["INSUFFICIENT_HISTORY"])
        if p_market <= 0 or p_market >= 1:
            return DpdsResult(None, None, ["MARKET_OUT_OF_BOUNDS"])
        if spot_mid <= 0:
            return DpdsResult(None, None, ["REF_INVALID"])

        state = self._states.setdefault(asset_id, DpdsState())
        logit_now = _logit(p_market)
        if state.last_ts_ms is None:
            state.last_ts_ms = now_ts_ms
            state.last_logit = logit_now
            state.last_spot = spot_mid
            return DpdsResult(None, None, ["INSUFFICIENT_HISTORY"])

        dt_sec = (now_ts_ms - state.last_ts_ms) / 1000.0
        if dt_sec <= 0:
            return DpdsResult(None, None, ["INSUFFICIENT_HISTORY"])

        spot_return = math.log(spot_mid / state.last_spot)
        d_logit = logit_now - state.last_logit

        state.var = ewma_variance_update(state.var, spot_return, dt_sec, self.half_life_sec)
        state.cov = ewma_cov_update(state.cov, d_logit, spot_return, dt_sec, self.half_life_sec)
        state.samples += 1
        state.last_ts_ms = now_ts_ms
        state.last_logit = logit_now
        state.last_spot = spot_mid

        if state.samples < self.min_samples:
            blockers.append("INSUFFICIENT_HISTORY")
        if state.var <= self.var_floor:
            blockers.append("VAR_TOO_SMALL")
        if blockers:
            return DpdsResult(None, None, blockers)

        beta = state.cov / state.var if state.var != 0 else None
        if beta is None or not math.isfinite(beta):
            return DpdsResult(None, None, ["VAR_TOO_SMALL"])
        dpds = beta * p_market * (1.0 - p_market)
        return DpdsResult(dpds=dpds, beta_logit=beta, blockers=[])


def ewma_cov_update(prev_cov: float, x: float, y: float, dt_sec: float, half_life_sec: float) -> float:
    if half_life_sec <= 0:
        return x * y
    alpha = 1.0 - math.exp(-math.log(2.0) * dt_sec / half_life_sec)
    return (1.0 - alpha) * prev_cov + alpha * (x * y)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))
