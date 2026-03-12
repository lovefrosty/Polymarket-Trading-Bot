from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SizePlan:
    buy_amount: float
    sell_amount: float


def get_buy_sell_amount(
    *,
    position: float,
    max_size: float,
    trade_size: float,
    avg_price: float,
    reverse_position: float = 0.0,
    reverse_position_min_size: float = 20.0,
    min_order_size: float = 0.0,
    usdc_balance: Optional[float] = None,
    buy_price: Optional[float] = None,
    hard_position_cap: float = 250.0,
) -> SizePlan:
    normalized_position = max(0.0, float(position))
    effective_max = min(float(max_size), float(hard_position_cap))
    desired_trade_size = max(0.0, float(trade_size))
    buy_amount = 0.0
    sell_amount = 0.0

    if (
        normalized_position < effective_max
        and normalized_position < float(hard_position_cap)
        and float(reverse_position) <= float(reverse_position_min_size)
    ):
        buy_amount = min(desired_trade_size, effective_max - normalized_position)
        if usdc_balance is not None and buy_price is not None and buy_price > 0:
            affordable = max(0.0, float(usdc_balance) / float(buy_price))
            buy_amount = min(buy_amount, affordable)
        if buy_amount < float(min_order_size):
            buy_amount = 0.0

    if normalized_position > 0 and float(avg_price) > 0:
        sell_amount = min(normalized_position, desired_trade_size)
        if sell_amount < float(min_order_size):
            sell_amount = 0.0

    return SizePlan(buy_amount=float(buy_amount), sell_amount=float(sell_amount))
