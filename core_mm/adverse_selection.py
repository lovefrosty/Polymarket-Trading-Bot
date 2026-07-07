from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class AdverseSelectionDecision:
    active: bool
    buy_blocked: bool
    reason: Optional[str]
    mode: str
    score: float
    threshold: float
    effective_min_price: Optional[float]
    effective_max_price: Optional[float]
    components: Dict[str, float]


def evaluate_tail_adverse_selection(
    *,
    mode: str,
    mid_price: float,
    quote_bid_price: Optional[float],
    best_bid: Optional[float],
    best_ask: Optional[float],
    bid_depth: float,
    ask_depth: float,
    trade_size: float,
    net_position: float,
    static_min_price: float,
    static_max_price: float,
    threshold: float,
    exit_cost_multiplier: float,
    spread_bps: Optional[float],
    ewma_imbalance_bps: float,
    fill_adversity_ratio: float,
    realized_vol_bps: float,
    three_hour_volatility: float,
    book_age_ms: Optional[int],
    stale_book_gate_ms: int,
    time_to_expiry_ms: Optional[int],
    market_duration_ms: Optional[int],
) -> AdverseSelectionDecision:
    """Score whether a new buy quote is exposed to adverse selection.

    The score is intentionally defensive. It treats price tailness as only one
    term, then asks whether exit cost, spread width, weak depth, adverse
    flow/momentum, recent adverse fills, volatility, staleness, or expiry
    pressure make the quoted edge insufficient.
    """

    active_mode = str(mode or "adaptive").strip().lower()
    if active_mode not in {"off", "static", "adaptive"}:
        active_mode = "adaptive"
    # Modes are operator/config choices, not market regimes:
    # - off: never block new buys from this guard.
    # - static: preserve the explicit fixed low/high boundaries.
    # - adaptive: compute an adverse-selection score from live book economics.
    p = _clamp(float(mid_price), 0.0, 1.0)
    risk_reducing_buy = float(net_position) < 0.0
    safe_threshold = max(0.01, float(threshold))
    components: Dict[str, float] = {
        "tail_pressure": _tail_pressure(p),
        "risk_reducing_buy": 1.0 if risk_reducing_buy else 0.0,
    }
    if active_mode == "off" or p <= 0.0:
        return AdverseSelectionDecision(
            active=False,
            buy_blocked=False,
            reason=None,
            mode=active_mode,
            score=0.0,
            threshold=safe_threshold,
            effective_min_price=None,
            effective_max_price=None,
            components=components,
        )

    static_min = _clamp(float(static_min_price), 0.0, 1.0)
    static_max = _clamp(float(static_max_price), 0.0, 1.0)
    if active_mode == "static":
        active = bool((static_min > 0.0 and p <= static_min) or (static_max < 1.0 and p >= static_max))
        reason = _side_reason(p, "static_boundary_no_new_risk") if active else None
        return AdverseSelectionDecision(
            active=active,
            buy_blocked=bool(active and not risk_reducing_buy),
            reason=reason,
            mode=active_mode,
            score=components["tail_pressure"],
            threshold=safe_threshold,
            effective_min_price=static_min,
            effective_max_price=static_max,
            components=components,
        )

    score_components = _adaptive_components(
        mid_price=p,
        quote_bid_price=quote_bid_price,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        trade_size=trade_size,
        spread_bps=spread_bps,
        ewma_imbalance_bps=ewma_imbalance_bps,
        fill_adversity_ratio=fill_adversity_ratio,
        realized_vol_bps=realized_vol_bps,
        three_hour_volatility=three_hour_volatility,
        book_age_ms=book_age_ms,
        stale_book_gate_ms=stale_book_gate_ms,
        time_to_expiry_ms=time_to_expiry_ms,
        market_duration_ms=market_duration_ms,
        exit_cost_multiplier=exit_cost_multiplier,
    )
    components.update(score_components)
    score = float(score_components["score"])
    effective_min, effective_max = _effective_boundaries(
        score_without_tail=float(score_components["score_without_tail"]),
        threshold=safe_threshold,
        tail_weight=float(score_components["tail_weight"]),
    )
    active = score >= safe_threshold
    reason = _side_reason(p, "adaptive_tail_adverse_selection") if active else None
    return AdverseSelectionDecision(
        active=active,
        buy_blocked=bool(active and not risk_reducing_buy),
        reason=reason,
        mode=active_mode,
        score=score,
        threshold=safe_threshold,
        effective_min_price=effective_min,
        effective_max_price=effective_max,
        components=components,
    )


def _adaptive_components(
    *,
    mid_price: float,
    quote_bid_price: Optional[float],
    best_bid: Optional[float],
    best_ask: Optional[float],
    bid_depth: float,
    ask_depth: float,
    trade_size: float,
    spread_bps: Optional[float],
    ewma_imbalance_bps: float,
    fill_adversity_ratio: float,
    realized_vol_bps: float,
    three_hour_volatility: float,
    book_age_ms: Optional[int],
    stale_book_gate_ms: int,
    time_to_expiry_ms: Optional[int],
    market_duration_ms: Optional[int],
    exit_cost_multiplier: float,
) -> Dict[str, float]:
    tail_pressure = _tail_pressure(mid_price)
    tail_denominator = max(0.01, min(mid_price, 1.0 - mid_price))
    quote_edge_bps = 0.0
    if quote_bid_price is not None:
        quote_edge_bps = max(0.0, (mid_price - float(quote_bid_price)) / tail_denominator * 10_000.0)
    exit_cost_bps = 0.0
    if best_bid is not None:
        exit_cost_bps = max(0.0, (mid_price - float(best_bid)) / tail_denominator * 10_000.0)
    required_edge_bps = exit_cost_bps * max(1.0, float(exit_cost_multiplier))
    edge_deficit_pressure = 0.0
    if required_edge_bps > 0.0:
        edge_deficit_pressure = _clamp((required_edge_bps - quote_edge_bps) / required_edge_bps, 0.0, 1.0)

    active_spread_bps = float(spread_bps or 0.0)
    if best_bid is not None and best_ask is not None and mid_price > 0.0:
        spread_mid_bps = max(0.0, (float(best_ask) - float(best_bid)) / mid_price * 10_000.0)
        spread_tail_bps = max(0.0, (float(best_ask) - float(best_bid)) / tail_denominator * 10_000.0)
        active_spread_bps = max(active_spread_bps, spread_mid_bps, spread_tail_bps)
    spread_pressure = _clamp(active_spread_bps / 1_500.0, 0.0, 1.0)

    exit_depth = max(0.0, float(bid_depth))
    hedge_depth = max(0.0, float(ask_depth))
    target_depth = max(1.0, float(trade_size) * 2.0)
    exit_depth_pressure = _clamp(1.0 - exit_depth / target_depth, 0.0, 1.0)
    hedge_depth_pressure = _clamp(1.0 - hedge_depth / target_depth, 0.0, 1.0)
    depth_pressure = max(exit_depth_pressure, hedge_depth_pressure * 0.5)

    flow_pressure = _clamp(max(0.0, -float(ewma_imbalance_bps)) / 10_000.0, 0.0, 1.0)
    adversity_pressure = _clamp(float(fill_adversity_ratio) / 0.65, 0.0, 1.0)
    vol_pressure = max(
        _clamp(float(realized_vol_bps) / 150.0, 0.0, 1.0),
        _clamp(float(three_hour_volatility) / 0.05, 0.0, 1.0),
    )
    stale_pressure = 0.0
    if book_age_ms is not None and stale_book_gate_ms > 0:
        stale_pressure = _clamp(float(book_age_ms) / float(stale_book_gate_ms), 0.0, 1.0)
    expiry_pressure = _expiry_pressure(time_to_expiry_ms=time_to_expiry_ms, market_duration_ms=market_duration_ms)

    weights = {
        "tail": 0.40,
        "edge_deficit": 0.20,
        "spread": 0.15,
        "depth": 0.10,
        "flow": 0.10,
        "adversity": 0.15,
        "vol": 0.10,
        "stale": 0.05,
        "expiry": 0.10,
    }
    tail_score = weights["tail"] * tail_pressure
    score_without_tail = (
        weights["edge_deficit"] * edge_deficit_pressure
        + weights["spread"] * spread_pressure
        + weights["depth"] * depth_pressure
        + weights["flow"] * flow_pressure
        + weights["adversity"] * adversity_pressure
        + weights["vol"] * vol_pressure
        + weights["stale"] * stale_pressure
        + weights["expiry"] * expiry_pressure
    )
    score = tail_score + score_without_tail
    return {
        "tail_pressure": round(tail_pressure, 8),
        "tail_weight": weights["tail"],
        "tail_score": round(tail_score, 8),
        "score_without_tail": round(score_without_tail, 8),
        "score": round(score, 8),
        "quote_edge_bps": round(quote_edge_bps, 8),
        "exit_cost_bps": round(exit_cost_bps, 8),
        "required_edge_bps": round(required_edge_bps, 8),
        "edge_deficit_pressure": round(edge_deficit_pressure, 8),
        "spread_bps": round(active_spread_bps, 8),
        "spread_pressure": round(spread_pressure, 8),
        "exit_depth_pressure": round(exit_depth_pressure, 8),
        "hedge_depth_pressure": round(hedge_depth_pressure, 8),
        "depth_pressure": round(depth_pressure, 8),
        "flow_pressure": round(flow_pressure, 8),
        "adversity_pressure": round(adversity_pressure, 8),
        "vol_pressure": round(vol_pressure, 8),
        "stale_pressure": round(stale_pressure, 8),
        "expiry_pressure": round(expiry_pressure, 8),
    }


def _effective_boundaries(*, score_without_tail: float, threshold: float, tail_weight: float) -> tuple[float, float]:
    if tail_weight <= 0.0:
        return 0.0, 1.0
    required_tail_pressure = (float(threshold) - float(score_without_tail)) / float(tail_weight)
    required_tail_pressure = _clamp(required_tail_pressure, 0.0, 1.0)
    low = 0.5 * (1.0 - required_tail_pressure)
    high = 1.0 - low
    return round(low, 8), round(high, 8)


def _tail_pressure(price: float) -> float:
    return _clamp(abs(float(price) - 0.5) / 0.5, 0.0, 1.0)


def _expiry_pressure(*, time_to_expiry_ms: Optional[int], market_duration_ms: Optional[int]) -> float:
    if time_to_expiry_ms is None or market_duration_ms is None:
        return 0.0
    duration = max(1.0, float(market_duration_ms))
    window = max(60_000.0, duration * 0.20)
    remaining = max(0.0, float(time_to_expiry_ms))
    if remaining >= window:
        return 0.0
    return _clamp(1.0 - remaining / window, 0.0, 1.0)


def _side_reason(price: float, prefix: str) -> str:
    side = "low" if float(price) < 0.5 else "high"
    return f"{prefix}_{side}"


def _clamp(value: float, low: float, high: float) -> float:
    return min(float(high), max(float(low), float(value)))
