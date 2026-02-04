from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SizingConfig:
    kelly_fraction_max: float


def kelly_fraction(p_hat: float, price: float, side: str) -> float:
    denom = price * (1.0 - price)
    if denom <= 0:
        return 0.0
    if side == "buy":
        return (p_hat - price) / denom
    if side == "sell":
        return (price - p_hat) / denom
    return 0.0


def size_from_kelly(
    equity: float,
    p_hat: float,
    price: float,
    side: str,
    cfg: SizingConfig,
    max_size: Optional[float],
    confidence: float = 1.0,
) -> float:
    frac = kelly_fraction(p_hat, price, side)
    if frac <= 0:
        return 0.0
    frac = max(0.0, min(1.0, confidence)) * frac
    frac = min(frac, cfg.kelly_fraction_max)
    size = (equity * frac) / max(price, 1e-6)
    if max_size is None:
        return size
    return min(size, max_size)
