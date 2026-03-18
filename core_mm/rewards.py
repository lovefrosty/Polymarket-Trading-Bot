"""Polymarket liquidity rewards scorer and optimizer.

The quadratic scoring formula used by Polymarket for liquidity rewards:

    S(v, s) = ((v - s) / v)^2 * b

where:
    v = max_incentive_spread (per-side, from market config)
    s = order distance from midpoint
    b = order size

Total reward score = min(buy_score, sell_score).
Orders outside the max_incentive_spread score zero.

Key insights:
- Being 2x tighter → ~4x score (quadratic)
- Both sides must score equally (min constrains total)
- Sampled every minute; maximize uptime
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RewardsConfig:
    """Configuration for rewards optimization."""

    enabled: bool = False
    # Max ticks to tighten quotes toward mid for rewards.
    # 0 = no tightening (just score/diagnose).
    max_tighten_ticks: int = 0


@dataclass(frozen=True)
class RewardsScore:
    """Reward score for a pair of quotes."""

    buy_score: float = 0.0
    sell_score: float = 0.0
    total_score: float = 0.0  # min(buy, sell)
    buy_distance: float = 0.0  # Absolute distance from mid
    sell_distance: float = 0.0
    max_spread: float = 0.0  # The v parameter
    eligible: bool = False  # Whether max_incentive_spread is set


def compute_reward_score(
    *,
    mid_price: float,
    bid_price: Optional[float],
    ask_price: Optional[float],
    bid_size: float = 0.0,
    ask_size: float = 0.0,
    max_incentive_spread: Optional[float] = None,
) -> RewardsScore:
    """Compute quadratic reward score for a bid/ask pair.

    Args:
        mid_price: Current midpoint price.
        bid_price: Our bid (buy) price.
        ask_price: Our ask (sell) price.
        bid_size: Our bid size.
        ask_size: Our ask size.
        max_incentive_spread: Market's max spread for rewards eligibility.
            This is the full spread; per-side max is half.

    Returns:
        RewardsScore with buy/sell/total scores.
    """
    if max_incentive_spread is None or max_incentive_spread <= 0 or mid_price <= 0:
        return RewardsScore()

    # Per-side max distance from mid
    v = float(max_incentive_spread) / 2.0
    if v <= 0:
        return RewardsScore()

    buy_dist = (float(mid_price) - float(bid_price)) if bid_price is not None else v + 1
    sell_dist = (float(ask_price) - float(mid_price)) if ask_price is not None else v + 1

    buy_score = _quadratic_score(v, buy_dist, float(bid_size))
    sell_score = _quadratic_score(v, sell_dist, float(ask_size))
    total = min(buy_score, sell_score)

    return RewardsScore(
        buy_score=buy_score,
        sell_score=sell_score,
        total_score=total,
        buy_distance=max(0.0, buy_dist),
        sell_distance=max(0.0, sell_dist),
        max_spread=float(max_incentive_spread),
        eligible=True,
    )


def compute_tighten_ticks(
    *,
    mid_price: float,
    bid_price: Optional[float],
    ask_price: Optional[float],
    bid_size: float,
    ask_size: float,
    max_incentive_spread: Optional[float],
    tick_size: float,
    max_tighten: int = 2,
) -> int:
    """Compute how many ticks to tighten quotes for optimal rewards.

    Returns the number of ticks to shift BOTH bid up and ask down.
    Only tightens if the reward score improvement justifies it.
    Never crosses the midpoint.
    """
    if max_incentive_spread is None or max_incentive_spread <= 0:
        return 0
    if mid_price <= 0 or tick_size <= 0 or max_tighten <= 0:
        return 0
    if bid_price is None or ask_price is None:
        return 0

    current = compute_reward_score(
        mid_price=mid_price, bid_price=bid_price, ask_price=ask_price,
        bid_size=bid_size, ask_size=ask_size,
        max_incentive_spread=max_incentive_spread,
    )

    best_ticks = 0
    best_score = current.total_score

    for t in range(1, max_tighten + 1):
        new_bid = float(bid_price) + t * float(tick_size)
        new_ask = float(ask_price) - t * float(tick_size)
        # Don't cross or touch mid
        if new_bid >= float(mid_price) or new_ask <= float(mid_price):
            break
        # Don't cross each other
        if new_bid >= new_ask:
            break
        candidate = compute_reward_score(
            mid_price=mid_price, bid_price=new_bid, ask_price=new_ask,
            bid_size=bid_size, ask_size=ask_size,
            max_incentive_spread=max_incentive_spread,
        )
        if candidate.total_score > best_score * 1.1:  # 10% improvement threshold
            best_score = candidate.total_score
            best_ticks = t

    return best_ticks


def _quadratic_score(v: float, s: float, b: float) -> float:
    """S(v, s) = ((v - s) / v)^2 * b"""
    if v <= 0 or b <= 0:
        return 0.0
    if s >= v:
        return 0.0
    ratio = (v - s) / v
    return ratio * ratio * b
