import pytest

from core_mm.adverse_selection import evaluate_tail_adverse_selection


def _decision(**overrides):
    params = {
        "mode": "adaptive",
        "mid_price": 0.10,
        "quote_bid_price": 0.09,
        "best_bid": 0.09,
        "best_ask": 0.11,
        "bid_depth": 150.0,
        "ask_depth": 160.0,
        "trade_size": 50.0,
        "net_position": 0.0,
        "static_min_price": 0.10,
        "static_max_price": 0.90,
        "threshold": 0.50,
        "exit_cost_multiplier": 1.25,
        "spread_bps": 2_000.0,
        "ewma_imbalance_bps": 0.0,
        "fill_adversity_ratio": 0.0,
        "realized_vol_bps": 0.0,
        "three_hour_volatility": 0.0,
        "book_age_ms": 100,
        "stale_book_gate_ms": 5_000,
        "time_to_expiry_ms": None,
        "market_duration_ms": None,
    }
    params.update(overrides)
    return evaluate_tail_adverse_selection(**params)


def test_adaptive_tail_guard_blocks_when_exit_cost_exceeds_edge() -> None:
    decision = _decision()

    assert decision.active is True
    assert decision.buy_blocked is True
    assert decision.reason == "adaptive_tail_adverse_selection_low"
    assert decision.score >= decision.threshold
    assert decision.components["edge_deficit_pressure"] > 0.0


def test_adaptive_tail_guard_allows_economic_tail_quote() -> None:
    decision = _decision(
        quote_bid_price=0.099,
        best_bid=0.099,
        best_ask=0.101,
        bid_depth=2_000.0,
        ask_depth=2_000.0,
        spread_bps=200.0,
        exit_cost_multiplier=1.0,
    )

    assert decision.active is False
    assert decision.buy_blocked is False
    assert decision.score < decision.threshold


def test_adverse_flow_moves_effective_boundary_inward() -> None:
    calm = _decision(
        mid_price=0.23,
        quote_bid_price=0.22,
        best_bid=0.22,
        best_ask=0.24,
        bid_depth=300.0,
        ask_depth=300.0,
        spread_bps=870.0,
        ewma_imbalance_bps=0.0,
    )
    adverse = _decision(
        mid_price=0.23,
        quote_bid_price=0.22,
        best_bid=0.22,
        best_ask=0.24,
        bid_depth=25.0,
        ask_depth=300.0,
        spread_bps=870.0,
        ewma_imbalance_bps=-8_000.0,
        fill_adversity_ratio=0.70,
    )

    assert calm.active is False
    assert adverse.active is True
    assert adverse.effective_min_price is not None
    assert calm.effective_min_price is not None
    assert adverse.effective_min_price > calm.effective_min_price


def test_risk_reducing_buy_is_not_blocked() -> None:
    decision = _decision(net_position=-20.0)

    assert decision.active is True
    assert decision.buy_blocked is False


def test_static_mode_preserves_explicit_boundary() -> None:
    decision = _decision(mode="static", mid_price=0.12, static_min_price=0.15)

    assert decision.active is True
    assert decision.buy_blocked is True
    assert decision.effective_min_price == pytest.approx(0.15)
