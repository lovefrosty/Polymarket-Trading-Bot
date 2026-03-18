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
    # Inventory skew: shift quote center toward neutrality.
    # Positive → long → both shift DOWN (ask cheaper, bid less aggressive).
    # Negative → short → both shift UP.
    inventory_skew_ticks: int = 0,
    # Spread multiplier: > 1.0 widens the spread symmetrically from mid.
    # Used by the graduated staleness gate (caution zone → 2.0).
    spread_multiplier: float = 1.0,
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

    # Asymmetric inventory skew: when long, ease the ask harder than the bid.
    # Long (positive ticks): ask_skew = 1.5x, bid_skew = 0.5x → sells cheaper.
    # Short (negative ticks): bid_skew = 1.5x, ask_skew = 0.5x → buys cheaper.
    if inventory_skew_ticks != 0:
        if inventory_skew_ticks > 0:
            bid_skew = round(inventory_skew_ticks * 0.5)
            ask_skew = round(inventory_skew_ticks * 1.5)
        else:
            bid_skew = round(inventory_skew_ticks * 1.5)
            ask_skew = round(inventory_skew_ticks * 0.5)
        if bid_price is not None:
            bid_price = clamp_price(bid_price - bid_skew * active_tick)
            bid_mode = f"{bid_mode}_skewed"
        if ask_price is not None:
            ask_price = clamp_price(ask_price - ask_skew * active_tick)
            ask_mode = f"{ask_mode}_skewed"
        # Guard: asymmetric offsets can push ask below bid
        if bid_price is not None and ask_price is not None and bid_price >= ask_price:
            raw_mid = (
                (metrics.best_bid + metrics.best_ask) / 2.0
                if metrics.best_bid is not None and metrics.best_ask is not None
                else None
            )
            if raw_mid is not None:
                bid_price = clamp_price(round_to_tick(raw_mid - active_tick, active_tick, mode="down"))
                ask_price = clamp_price(round_to_tick(raw_mid + active_tick, active_tick, mode="up"))
            bid_mode = "fallback_skew_inversion"
            ask_mode = "fallback_skew_inversion"

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

    # Spread multiplier: widen symmetrically around the computed mid.
    # Used by the graduated staleness gate (caution zone → 2x spread).
    if spread_multiplier != 1.0 and bid_price is not None and ask_price is not None:
        mid = (bid_price + ask_price) / 2.0
        bid_price = clamp_price(round_to_tick(mid - (mid - bid_price) * spread_multiplier, active_tick, mode="down"))
        ask_price = clamp_price(round_to_tick(mid + (ask_price - mid) * spread_multiplier, active_tick, mode="up"))

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


def compute_inventory_skew_ticks(
    position: float,
    max_size: float,
    max_skew_ticks: int = 3,
    avg_cost: float = 0.0,
    mid_price: float = 0.0,
) -> int:
    """
    Compute ticks to shift the quote center toward neutrality.

    Positive → long → shift both prices DOWN (easier to sell).
    Negative → short → shift both prices UP (easier to buy).

    P&L urgency: underwater positions get up to 2x urgency (exit faster);
    profitable positions get down to 0.5x (hold longer).
    """
    if max_size <= 0.0:
        return 0
    inventory_ratio = float(position) / float(max_size)
    clamped = max(-1.0, min(1.0, inventory_ratio))

    pnl_urgency = 1.0
    if float(avg_cost) > 0.0 and float(position) > 0.0 and float(mid_price) > 0.0:
        unrealized_pnl_pct = (float(mid_price) - float(avg_cost)) / float(avg_cost)
        # Underwater (negative pnl_pct): urgency > 1 (up to 2x)
        # In profit (positive pnl_pct): urgency < 1 (down to 0.5x)
        pnl_urgency = max(0.5, min(2.0, 1.0 - unrealized_pnl_pct * 2.0))

    return round(clamped * int(max_skew_ticks) * pnl_urgency)


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
