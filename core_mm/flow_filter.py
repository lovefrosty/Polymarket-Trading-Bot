from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowFilterDecision:
    volume_ratio: float
    allow_buy: bool
    allow_sell: bool
    reason: str


def evaluate_volume_ratio(
    bid_volume: float,
    ask_volume: float,
    *,
    suppress_buy_below: float = 0.7,
    suppress_sell_above: float = 1.4,
) -> FlowFilterDecision:
    bid = max(0.0, float(bid_volume))
    ask = max(0.0, float(ask_volume))

    if bid == 0.0 and ask == 0.0:
        return FlowFilterDecision(volume_ratio=1.0, allow_buy=False, allow_sell=False, reason="no_near_mid_depth")
    if ask == 0.0:
        return FlowFilterDecision(volume_ratio=float("inf"), allow_buy=True, allow_sell=False, reason="buy_pressure_only")
    if bid == 0.0:
        return FlowFilterDecision(volume_ratio=0.0, allow_buy=False, allow_sell=True, reason="sell_pressure_only")

    ratio = bid / ask
    if ratio < float(suppress_buy_below):
        return FlowFilterDecision(volume_ratio=ratio, allow_buy=False, allow_sell=True, reason="suppress_buys")
    if ratio > float(suppress_sell_above):
        return FlowFilterDecision(volume_ratio=ratio, allow_buy=True, allow_sell=False, reason="suppress_sells")
    return FlowFilterDecision(volume_ratio=ratio, allow_buy=True, allow_sell=True, reason="two_sided")
