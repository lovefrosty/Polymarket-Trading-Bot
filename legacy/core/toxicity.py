from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class ToxicityResult:
    tox_bps: Optional[float]
    blockers: Sequence[str]


def toxicity_bps(side: str, price_now: Optional[float], price_future: Optional[float]) -> ToxicityResult:
    if price_now is None or price_future is None:
        return ToxicityResult(None, ["TOX_UNAVAILABLE"])
    side_norm = side.lower()
    if side_norm == "buy":
        tox = max(0.0, price_future - price_now)
    elif side_norm == "sell":
        tox = max(0.0, price_now - price_future)
    else:
        return ToxicityResult(None, ["UNKNOWN_SIDE"])
    return ToxicityResult(tox * 10000.0, [])


def toxicity_from_exec_prices(
    side: str,
    exec_price_now: Optional[float],
    exec_price_future: Optional[float],
) -> ToxicityResult:
    return toxicity_bps(side, exec_price_now, exec_price_future)
