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
    net_position: Optional[float] = None,
    min_order_size: float = 0.0,
    usdc_balance: Optional[float] = None,
    buy_price: Optional[float] = None,
    sell_price: Optional[float] = None,
    hard_position_cap: float = 250.0,
    # Inventory skew: scale buy/sell aggressiveness continuously based on
    # how long or short we are relative to max_size.
    # skew_factor=1.0 means: at 100% net_position / max_size, buy drops to 0
    # and sell scales to 2x trade_size (capped at position).
    # skew_factor=0.0 disables the continuous skew (original behaviour).
    inventory_skew_factor: float = 1.0,
    # Kelly criterion sizing: compute position sizes from edge and fair value
    p_fair: Optional[float] = None,
    kelly_fraction: float = 0.0,
    bankroll: Optional[float] = None,
) -> SizePlan:
    normalized_position = max(0.0, float(position))
    effective_max = min(float(max_size), float(hard_position_cap))
    desired_trade_size = max(0.0, float(trade_size))

    # Net exposure: this_token - other_token.
    # Positive = net long this token, negative = net short (other token held more).
    # When net short, buying this token REDUCES risk.
    computed_net = float(net_position) if net_position is not None else (float(position) - float(reverse_position))
    effective_net = max(0.0, computed_net)

    # --- Kelly criterion sizing ---
    # Compute separate buy and sell sizes based on edge (p_fair vs market price).
    # If Kelly is disabled (fraction=0), these stay equal to desired_trade_size.
    buy_trade_size = desired_trade_size
    sell_trade_size = desired_trade_size
    if p_fair is not None and kelly_fraction > 0.0 and bankroll is not None and bankroll > 0.0:
        clamped_pfair = max(0.01, min(0.99, float(p_fair)))
        # f_buy = (p_fair - buy_price) / (1 - buy_price)  [Kelly for buying YES]
        if buy_price is not None and 0.0 < buy_price < 1.0:
            f_buy = max(0.0, (clamped_pfair - buy_price) / (1.0 - buy_price))
            buy_trade_size = kelly_fraction * f_buy * float(bankroll)
        # f_sell = (sell_price - p_fair) / sell_price  [Kelly for selling YES = buying NO]
        if sell_price is not None and 0.0 < sell_price < 1.0:
            f_sell = max(0.0, (sell_price - clamped_pfair) / sell_price)
            sell_trade_size = kelly_fraction * f_sell * float(bankroll)

    buy_amount = 0.0
    sell_amount = 0.0

    if normalized_position < float(hard_position_cap) and effective_net < effective_max:
        base_buy = min(buy_trade_size, effective_max - effective_net)

        # Inventory skew: reduce buy size proportionally as net exposure grows.
        if float(inventory_skew_factor) > 0.0 and effective_max > 0.0:
            long_ratio = min(1.0, effective_net / effective_max)
            buy_scale = max(0.0, 1.0 - long_ratio * float(inventory_skew_factor))
            base_buy = base_buy * buy_scale

        if usdc_balance is not None and buy_price is not None and buy_price > 0:
            affordable = max(0.0, float(usdc_balance) / float(buy_price))
            base_buy = min(base_buy, affordable)
        if base_buy < float(min_order_size):
            base_buy = 0.0
        buy_amount = base_buy

    # Sell section uses ACTUAL position (can only sell what you hold).
    if normalized_position > 0 and float(avg_price) > 0:
        base_sell = min(normalized_position, sell_trade_size)

        # Inventory skew: boost sell size as net exposure grows.
        if float(inventory_skew_factor) > 0.0 and effective_max > 0.0:
            long_ratio = min(1.0, effective_net / effective_max)
            sell_scale = 1.0 + long_ratio * float(inventory_skew_factor)
            base_sell = min(normalized_position, desired_trade_size * sell_scale)

        if base_sell < float(min_order_size):
            base_sell = 0.0
        sell_amount = base_sell

    return SizePlan(buy_amount=float(buy_amount), sell_amount=float(sell_amount))
