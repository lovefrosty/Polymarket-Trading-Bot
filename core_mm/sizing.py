from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SizePlan:
    buy_amount: float
    sell_amount: float
    buy_limiter: str = "trade_size"
    sell_limiter: str = "trade_size"
    buy_limiters: str = ""
    sell_limiters: str = ""


def get_buy_sell_amount(
    *,
    position: float,
    max_size: float,
    trade_size: float,
    avg_price: float,
    reverse_position: float = 0.0,
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
    risk_per_trade_budget: Optional[float] = None,
    risk_based_share_sizing: bool = True,
) -> SizePlan:
    def _apply_limit(
        amount: float,
        limited_amount: float,
        code: str,
        applied: list[str],
        primary: str,
    ) -> tuple[float, str]:
        if limited_amount < amount - 1e-9:
            if code not in applied:
                applied.append(code)
            return limited_amount, code
        return amount, primary

    normalized_position = max(0.0, float(position))
    effective_max = min(float(max_size), float(hard_position_cap))
    desired_trade_size = max(0.0, float(trade_size))

    # Net exposure: this_token - other_token.
    # Positive = net long this token, negative = net short (other token held more).
    # When net short, buying this token REDUCES risk.
    computed_net = float(net_position) if net_position is not None else (float(position) - float(reverse_position))
    effective_net = float(computed_net)
    long_ratio = min(1.0, max(0.0, effective_net) / effective_max) if effective_max > 0.0 else 0.0
    short_ratio = min(1.0, max(0.0, -effective_net) / effective_max) if effective_max > 0.0 else 0.0

    # --- Kelly criterion sizing ---
    # Compute separate buy and sell sizes based on edge (p_fair vs market price).
    # Kelly produces notional dollars, so convert to shares before using it as a
    # soft size scaler.
    buy_trade_size = desired_trade_size
    sell_trade_size = desired_trade_size
    if p_fair is not None and kelly_fraction > 0.0 and bankroll is not None and bankroll > 0.0:
        clamped_pfair = max(0.01, min(0.99, float(p_fair)))
        # f_buy = (p_fair - buy_price) / (1 - buy_price)  [Kelly for buying YES]
        if buy_price is not None and 0.0 < buy_price < 1.0:
            f_buy = max(0.0, (clamped_pfair - buy_price) / (1.0 - buy_price))
            buy_notional = kelly_fraction * f_buy * float(bankroll)
            buy_trade_size = buy_notional / float(buy_price) if buy_price > 0.0 else 0.0
        # f_sell = (sell_price - p_fair) / sell_price  [Kelly for selling YES = buying NO]
        if sell_price is not None and 0.0 < sell_price < 1.0:
            f_sell = max(0.0, (sell_price - clamped_pfair) / sell_price)
            sell_notional = kelly_fraction * f_sell * float(bankroll)
            sell_trade_size = sell_notional / float(sell_price) if sell_price > 0.0 else 0.0

        if buy_trade_size > 0.0:
            buy_trade_size = min(desired_trade_size, buy_trade_size)
        if sell_trade_size > 0.0:
            sell_trade_size = min(desired_trade_size, sell_trade_size)

    risk_buy_cap = None
    if (
        risk_based_share_sizing
        and risk_per_trade_budget is not None
        and float(risk_per_trade_budget) > 0.0
        and buy_price is not None
        and float(buy_price) > 0.0
    ):
        risk_buy_cap = float(risk_per_trade_budget) / float(buy_price)

    buy_amount = 0.0
    sell_amount = 0.0
    buy_limiters: list[str] = []
    sell_limiters: list[str] = []
    buy_limiter = "trade_size"
    sell_limiter = "trade_size"

    if normalized_position < float(hard_position_cap):
        remaining_headroom = max(0.0, effective_max - max(0.0, effective_net))
        explain_buy = desired_trade_size
        explain_buy, buy_limiter = _apply_limit(explain_buy, remaining_headroom, "net_headroom", buy_limiters, buy_limiter)
        if risk_buy_cap is not None:
            explain_buy, buy_limiter = _apply_limit(explain_buy, float(risk_buy_cap), "risk_budget", buy_limiters, buy_limiter)
        explain_buy, buy_limiter = _apply_limit(explain_buy, buy_trade_size, "kelly", buy_limiters, buy_limiter)

        base_buy = min(buy_trade_size, remaining_headroom)
        if risk_buy_cap is not None:
            base_buy = min(base_buy, float(risk_buy_cap))

        # Inventory skew: reduce buys when long, boost them when net short so
        # the strategy can reduce short-side risk symmetrically.
        if float(inventory_skew_factor) > 0.0 and effective_max > 0.0:
            if effective_net >= 0.0:
                buy_scale = max(0.0, 1.0 - long_ratio * float(inventory_skew_factor))
            else:
                buy_scale = 1.0 + short_ratio * float(inventory_skew_factor)
            skewed_buy = base_buy * buy_scale
            _, buy_limiter = _apply_limit(base_buy, skewed_buy, "inventory_skew", buy_limiters, buy_limiter)
            base_buy = skewed_buy

        if usdc_balance is not None and buy_price is not None and buy_price > 0:
            affordable = max(0.0, float(usdc_balance) / float(buy_price))
            base_buy, buy_limiter = _apply_limit(base_buy, affordable, "affordability", buy_limiters, buy_limiter)
        if base_buy < float(min_order_size):
            if "min_order_size" not in buy_limiters:
                buy_limiters.append("min_order_size")
            buy_limiter = "min_order_size"
            base_buy = 0.0
        buy_amount = base_buy
    else:
        buy_limiters = ["hard_position_cap"]
        buy_limiter = "hard_position_cap"

    # Sell section uses ACTUAL position (can only sell what you hold).
    if normalized_position > 0 and float(avg_price) > 0:
        explain_sell = desired_trade_size
        explain_sell, sell_limiter = _apply_limit(explain_sell, normalized_position, "inventory", sell_limiters, sell_limiter)
        explain_sell, sell_limiter = _apply_limit(explain_sell, sell_trade_size, "kelly", sell_limiters, sell_limiter)

        base_sell = min(normalized_position, sell_trade_size)

        # Inventory skew: boost sells when long, damp them when net short so
        # selling cannot amplify short-side exposure as aggressively.
        if float(inventory_skew_factor) > 0.0 and effective_max > 0.0:
            if effective_net >= 0.0:
                sell_scale = 1.0 + long_ratio * float(inventory_skew_factor)
            else:
                sell_scale = max(0.0, 1.0 - short_ratio * float(inventory_skew_factor))
            skewed_sell = min(normalized_position, sell_trade_size * sell_scale)
            _, sell_limiter = _apply_limit(base_sell, skewed_sell, "inventory_skew", sell_limiters, sell_limiter)
            base_sell = skewed_sell

        if base_sell < float(min_order_size):
            if "min_order_size" not in sell_limiters:
                sell_limiters.append("min_order_size")
            sell_limiter = "min_order_size"
            base_sell = 0.0
        sell_amount = base_sell
    elif normalized_position <= 0.0:
        sell_limiters = ["inventory"]
        sell_limiter = "inventory"
    else:
        sell_limiters = ["avg_price_missing"]
        sell_limiter = "avg_price_missing"

    if not buy_limiters and buy_amount > 0.0:
        buy_limiters = ["trade_size"]
    if not sell_limiters and sell_amount > 0.0:
        sell_limiters = ["trade_size"]

    return SizePlan(
        buy_amount=float(buy_amount),
        sell_amount=float(sell_amount),
        buy_limiter=str(buy_limiter),
        sell_limiter=str(sell_limiter),
        buy_limiters=",".join(buy_limiters),
        sell_limiters=",".join(sell_limiters),
    )
