from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

from core.reference_price import ValidatedPrice
from core.volatility import ewma_variance_update


@dataclass(frozen=True)
class ReferenceSignalSnapshot:
    r_t: Optional[float]
    sigma_t: Optional[float]
    z_mom: Optional[float]
    last_price: Optional[float]
    last_update_mono_ns: Optional[int]


class ReferenceSignalState:
    def __init__(self, half_life_sec: float) -> None:
        self.half_life_sec = half_life_sec
        self._last_price: Optional[float] = None
        self._last_mono_ns: Optional[int] = None
        self._var: float = 0.0
        self._snapshot = ReferenceSignalSnapshot(
            r_t=None, sigma_t=None, z_mom=None, last_price=None, last_update_mono_ns=None
        )

    def snapshot(self) -> ReferenceSignalSnapshot:
        return self._snapshot

    def update(
        self, price: ValidatedPrice, decision_mono_ns: int
    ) -> ReferenceSignalSnapshot:
        if price.t_recv_mono_ns > decision_mono_ns:
            return self._snapshot

        if self._last_mono_ns is not None and price.t_recv_mono_ns <= self._last_mono_ns:
            return self._snapshot

        if self._last_price is None or self._last_mono_ns is None:
            self._last_price = price.value
            self._last_mono_ns = price.t_recv_mono_ns
            self._snapshot = ReferenceSignalSnapshot(
                r_t=None,
                sigma_t=None,
                z_mom=None,
                last_price=price.value,
                last_update_mono_ns=price.t_recv_mono_ns,
            )
            return self._snapshot

        dt_sec = (price.t_recv_mono_ns - self._last_mono_ns) / 1_000_000_000.0
        if dt_sec <= 0:
            return self._snapshot

        r_t = _log_return(self._last_price, price.value)
        var = self._var
        sigma_t = None
        z_mom = None
        if r_t is not None:
            var = ewma_variance_update(var, r_t, dt_sec, self.half_life_sec)
            sigma_t = math.sqrt(max(var, 0.0))
            if sigma_t > 0:
                z_mom = r_t / sigma_t

        self._var = var
        self._last_price = price.value
        self._last_mono_ns = price.t_recv_mono_ns
        self._snapshot = ReferenceSignalSnapshot(
            r_t=r_t,
            sigma_t=sigma_t,
            z_mom=z_mom,
            last_price=price.value,
            last_update_mono_ns=price.t_recv_mono_ns,
        )
        return self._snapshot


def _log_return(prev: float, current: float) -> Optional[float]:
    if prev <= 0 or current <= 0:
        return None
    return math.log(current / prev)
