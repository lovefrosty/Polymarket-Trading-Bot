from __future__ import annotations

import pytest

from core_mm.kalshi.fees import KalshiFeeSpec, calculate_kalshi_fee, infer_fee_spec, reported_kalshi_fee


def test_calculate_kalshi_quadratic_taker_fee_rounds_up_to_cent() -> None:
    result = calculate_kalshi_fee(
        price=0.55,
        contracts=10,
        fee_spec=KalshiFeeSpec(fee_type="quadratic", fee_multiplier=1.0),
        is_taker=True,
    )
    assert result.fee_usdc == pytest.approx(0.18)
    assert result.fee_bps == pytest.approx((0.18 / 5.5) * 10_000.0)
    assert result.fee_source == "model_fallback"


def test_calculate_kalshi_quadratic_with_maker_fees_uses_lower_coefficient() -> None:
    result = calculate_kalshi_fee(
        price=0.55,
        contracts=10,
        fee_spec=KalshiFeeSpec(fee_type="quadratic_with_maker_fees", fee_multiplier=1.0),
        is_taker=False,
    )
    assert result.fee_usdc == pytest.approx(0.05)
    assert result.fee_type == "quadratic_with_maker_fees"


def test_calculate_kalshi_flat_fee_uses_flat_coefficient() -> None:
    result = calculate_kalshi_fee(
        price=0.50,
        contracts=10,
        fee_spec=KalshiFeeSpec(fee_type="flat", fee_multiplier=1.0),
        is_taker=True,
    )
    assert result.fee_usdc == pytest.approx(0.09)


def test_infer_fee_spec_prefers_series_fee_fields() -> None:
    spec = infer_fee_spec({"series_fee_type": "quadratic_with_maker_fees", "series_fee_multiplier": 2})
    assert spec.fee_type == "quadratic_with_maker_fees"
    assert spec.fee_multiplier == pytest.approx(2.0)


def test_reported_kalshi_fee_reads_exchange_fee_cost() -> None:
    result = reported_kalshi_fee(
        {"fee_cost": "0.07", "fee_type": "quadratic", "fee_multiplier": 1.0},
        price=0.50,
        contracts=10,
    )
    assert result is not None
    assert result.fee_usdc == pytest.approx(0.07)
    assert result.fee_source == "exchange_reported"
