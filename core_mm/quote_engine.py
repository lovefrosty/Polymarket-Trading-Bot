from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

from core_mm.book_metrics import MeaningfulBBO


@dataclass(frozen=True)
class QuotePlan:
    bid_price: Optional[float]
    ask_price: Optional[float]
    bid_mode: str
    ask_mode: str
    tick_size: float


def get_order_prices(
    metrics: MeaningfulBBO,
    avg_cost: float = 0.0,
    tick_size: Optional[float] = None,
    min_size: float = 100.0,
) -> QuotePlan:
    active_tick = resolve_tick_size(
        reference_price=_first_not_none(metrics.best_bid, metrics.best_ask, metrics.top_bid, metrics.top_ask),
        explicit_tick_size=tick_size,
    )

    bid_price = None
    ask_price = None
    bid_mode = "skip"
    ask_mode = "skip"

    if metrics.best_bid is not None:
        if metrics.best_bid_size > 1.5 * float(min_size):
            bid_price = clamp_price(metrics.best_bid + active_tick)
            bid_mode = "improve"
        else:
            bid_price = clamp_price(metrics.best_bid)
            bid_mode = "join"

    if metrics.best_ask is not None:
        if metrics.best_ask_size > 1.5 * float(min_size):
            ask_price = clamp_price(metrics.best_ask - active_tick)
            ask_mode = "improve"
        else:
            ask_price = clamp_price(metrics.best_ask)
            ask_mode = "join"

    if bid_price is not None and metrics.best_ask is not None and bid_price >= metrics.best_ask:
        bid_price = clamp_price(metrics.top_bid) if metrics.top_bid is not None else None
        bid_mode = "fallback_top_bid"

    if ask_price is not None and avg_cost > 0:
        ask_price = max(ask_price, float(avg_cost))
        ask_price = clamp_price(round_to_tick(ask_price, active_tick, mode="up"))
        ask_mode = f"{ask_mode}_avg_cost_floor" if ask_mode != "skip" else "avg_cost_floor"

    if ask_price is not None and metrics.best_bid is not None and ask_price <= metrics.best_bid:
        ask_price = clamp_price(metrics.top_ask) if metrics.top_ask is not None else None
        ask_mode = "fallback_top_ask"

    if bid_price is not None:
        bid_price = clamp_price(round_to_tick(bid_price, active_tick, mode="down"))
    if ask_price is not None:
        ask_price = clamp_price(round_to_tick(ask_price, active_tick, mode="up"))

    return QuotePlan(
        bid_price=bid_price,
        ask_price=ask_price,
        bid_mode=bid_mode,
        ask_mode=ask_mode,
        tick_size=active_tick,
    )


def resolve_tick_size(reference_price: Optional[float], explicit_tick_size: Optional[float] = None) -> float:
    if explicit_tick_size is not None and explicit_tick_size > 0:
        return float(explicit_tick_size)
    if reference_price is None:
        return 0.01
    price = float(reference_price)
    if price >= 0.99 or price <= 0.01:
        return 0.0001
    if price > 0.96 or price < 0.04:
        return 0.001
    return 0.01


def round_to_tick(price: float, tick_size: float, mode: str) -> float:
    if tick_size <= 0:
        return float(price)
    scaled = float(price) / float(tick_size)
    if mode == "up":
        rounded = math.ceil(scaled - 1e-9)
    else:
        rounded = math.floor(scaled + 1e-9)
    return round(rounded * float(tick_size), 10)


def clamp_price(price: Optional[float]) -> Optional[float]:
    if price is None:
        return None
    return min(0.99, max(0.01, float(price)))


def _first_not_none(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return float(value)
    return None
