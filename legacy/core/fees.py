from __future__ import annotations

from typing import Literal
import math

FeeMode = Literal["MAKE", "TAKE"]
FeeStatus = Literal["ok", "not_fee_addressable", "unknown"]

_ANCHORS = [
    (0.01, 0.0),
    (0.10, 20.0),
    (0.25, 88.0),
    (0.40, 144.0),
    (0.50, 156.0),
    (0.60, 144.0),
    (0.80, 64.0),
    (0.95, 6.0),
    (0.99, 0.0),
]


def taker_fee_bps_piecewise(p_exec: float) -> float:
    """
    Deterministic taker fee curve (bps) for 15m crypto using piecewise linear interpolation.
    Input: executable YES price probability p_exec in [0,1].
    Output: fee in basis points (float).
    """
    if math.isnan(p_exec):
        raise ValueError("fee_price_nan")
    price = min(max(float(p_exec), 0.01), 0.99)
    for idx in range(len(_ANCHORS) - 1):
        p0, f0 = _ANCHORS[idx]
        p1, f1 = _ANCHORS[idx + 1]
        if price <= p1:
            if p1 == p0:
                return float(f0)
            t = (price - p0) / (p1 - p0)
            return float(f0 + t * (f1 - f0))
    return float(_ANCHORS[-1][1])


def fee_bps(
    p_exec: float,
    mode: FeeMode,
    fee_status: FeeStatus,
    *,
    unknown_multiplier: float = 1.20,
    model: Literal["piecewise", "parametric"] = "piecewise",
    gamma: float = 2.0,
) -> float:
    """
    Returns fee in basis points.
    - MAKE: 0.0
    - TAKE: taker fee curve (piecewise by default)
    - fee_status modifies taker fee via conservative multiplier when unknown
    """
    if mode not in {"MAKE", "TAKE"}:
        raise ValueError(f"fee_mode_invalid:{mode}")
    if fee_status not in {"ok", "not_fee_addressable", "unknown"}:
        raise ValueError(f"fee_status_invalid:{fee_status}")
    if mode == "MAKE":
        return 0.0
    if model == "parametric":
        price = min(max(float(p_exec), 0.01), 0.99)
        x = abs(price - 0.5) / 0.5
        fee = 156.0 * (1.0 - x) ** float(gamma)
    else:
        fee = taker_fee_bps_piecewise(p_exec)
    if fee_status == "unknown":
        fee *= float(unknown_multiplier)
    return float(fee)
