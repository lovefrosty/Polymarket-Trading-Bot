"""Tests for core_mm.alpha_overlay — Epic 3 alpha overlay signals."""
from __future__ import annotations

import pytest

from core_mm.alpha_overlay import (
    AlphaOverlayManager,
    AlphaSignal,
    BookImbalanceAlpha,
    ComplementArbitrage,
    DepthRatioChange,
    FillAsymmetry,
    VolatilityRegime,
)


# ── BookImbalanceAlpha ──────────────────────────────────────────────────────


class TestBookImbalanceAlpha:
    def test_zero_depth_returns_zero(self) -> None:
        bia = BookImbalanceAlpha()
        assert bia.update(0, 0) == 0

    def test_balanced_book_below_threshold(self) -> None:
        bia = BookImbalanceAlpha(activation_threshold=0.25)
        # Balanced: 100/100 → imbalance=0
        for _ in range(10):
            result = bia.update(100, 100)
        assert result == 0

    def test_bid_heavy_positive_skew(self) -> None:
        bia = BookImbalanceAlpha(ewma_span=5, max_extra_ticks=3, activation_threshold=0.2)
        # Drive imbalance to bid-heavy
        for _ in range(20):
            result = bia.update(900, 100)
        assert result > 0, "Bid-heavy should produce positive skew"
        assert result <= 3

    def test_ask_heavy_negative_skew(self) -> None:
        bia = BookImbalanceAlpha(ewma_span=5, max_extra_ticks=3, activation_threshold=0.2)
        for _ in range(20):
            result = bia.update(100, 900)
        assert result < 0, "Ask-heavy should produce negative skew"
        assert result >= -3

    def test_max_ticks_capped(self) -> None:
        bia = BookImbalanceAlpha(ewma_span=3, max_extra_ticks=2, activation_threshold=0.1)
        for _ in range(30):
            result = bia.update(1000, 1)
        assert abs(result) <= 2

    def test_ewma_smooths_noise(self) -> None:
        bia = BookImbalanceAlpha(ewma_span=20, activation_threshold=0.3)
        # Establish balanced baseline
        for _ in range(30):
            bia.update(100, 100)
        # Single noisy spike shouldn't trigger
        result = bia.update(900, 100)
        assert result == 0, "Single spike should be smoothed away"


# ── FillAsymmetry ───────────────────────────────────────────────────────────


class TestFillAsymmetry:
    def test_no_fills_no_widen(self) -> None:
        fa = FillAsymmetry()
        assert fa.adversity_ratio == 0.0
        assert fa.spread_multiplier == 1.0

    def test_few_fills_not_enough_data(self) -> None:
        fa = FillAsymmetry()
        fa.record_fill("buy", 0.52, 0.50)  # adverse
        fa.record_fill("buy", 0.52, 0.50)
        assert fa.adversity_ratio == 0.0, "Need >=5 fills"

    def test_adverse_buys_widen(self) -> None:
        fa = FillAsymmetry(window_size=10, widen_threshold=0.6, widen_multiplier=1.4)
        # All buys above mid = 100% adverse
        for i in range(10):
            fa.record_fill("buy", 0.55, 0.50)
        assert fa.adversity_ratio == 1.0
        assert fa.spread_multiplier == 1.4

    def test_adverse_sells_widen(self) -> None:
        fa = FillAsymmetry(window_size=10, widen_threshold=0.6)
        for _ in range(10):
            fa.record_fill("sell", 0.45, 0.50)  # sold below mid
        assert fa.adversity_ratio == 1.0
        assert fa.spread_multiplier > 1.0

    def test_favorable_fills_no_widen(self) -> None:
        fa = FillAsymmetry(window_size=10, widen_threshold=0.6)
        # Buys below mid = favorable
        for _ in range(10):
            fa.record_fill("buy", 0.48, 0.50)
        assert fa.adversity_ratio == 0.0
        assert fa.spread_multiplier == 1.0

    def test_mixed_fills(self) -> None:
        fa = FillAsymmetry(window_size=10, widen_threshold=0.7)
        # 5 adverse + 5 favorable = 50% < 70% threshold
        for _ in range(5):
            fa.record_fill("buy", 0.55, 0.50)
        for _ in range(5):
            fa.record_fill("buy", 0.45, 0.50)
        assert abs(fa.adversity_ratio - 0.5) < 0.01
        assert fa.spread_multiplier == 1.0

    def test_zero_mid_ignored(self) -> None:
        fa = FillAsymmetry()
        fa.record_fill("buy", 0.50, 0.0)
        assert len(fa._window) == 0


# ── VolatilityRegime ────────────────────────────────────────────────────────


class TestVolatilityRegime:
    def test_not_enough_data(self) -> None:
        vr = VolatilityRegime()
        vr.update(0.50)
        vr.update(0.51)
        assert vr.realized_vol_bps == 0.0
        assert vr.regime == "normal"  # Not enough data → default to normal

    def test_low_vol_tightens(self) -> None:
        vr = VolatilityRegime(low_vol_bps=30, low_vol_multiplier=0.8)
        # Very stable prices
        for i in range(30):
            vr.update(0.500 + (i % 2) * 0.0001)
        assert vr.regime == "low"
        assert vr.spread_multiplier == 0.8

    def test_high_vol_widens(self) -> None:
        vr = VolatilityRegime(high_vol_bps=40, high_vol_multiplier=1.5)
        # Wild swings: 0.50 ↔ 0.55 → ~45 bps stdev
        for i in range(30):
            price = 0.50 if i % 2 == 0 else 0.55
            vr.update(price)
        assert vr.realized_vol_bps > 40
        assert vr.regime == "high"
        assert vr.spread_multiplier == 1.5

    def test_normal_vol_no_change(self) -> None:
        vr = VolatilityRegime(low_vol_bps=10, high_vol_bps=200)
        # Moderate moves
        for i in range(30):
            vr.update(0.50 + (i % 3) * 0.002)
        assert vr.regime == "normal"
        assert vr.spread_multiplier == 1.0


# ── AlphaOverlayManager ────────────────────────────────────────────────────


class TestAlphaOverlayManager:
    def test_default_signal_neutral(self) -> None:
        mgr = AlphaOverlayManager()
        mgr.update_book(100, 100)
        # Need enough mid-price updates for vol regime to have data
        for _ in range(10):
            mgr.update_mid(0.50)
        signal = mgr.get_signal()
        assert isinstance(signal, AlphaSignal)
        assert signal.extra_skew_ticks == 0
        # With stable prices and enough data → low vol → 0.8x
        assert signal.vol_regime == "low"
        assert signal.spread_multiplier == 0.8

    def test_combined_signal_with_imbalance(self) -> None:
        mgr = AlphaOverlayManager(
            imbalance_ewma_span=5,
            imbalance_threshold=0.2,
            imbalance_max_ticks=2,
        )
        # Drive strong imbalance
        for _ in range(20):
            mgr.update_book(900, 100)
            mgr.update_mid(0.50)
        signal = mgr.get_signal()
        assert signal.extra_skew_ticks > 0

    def test_fill_adversity_widens(self) -> None:
        mgr = AlphaOverlayManager(
            fill_widen_threshold=0.6,
            fill_widen_multiplier=1.3,
        )
        for _ in range(10):
            mgr.record_fill("buy", 0.55, 0.50)
        mgr.update_book(100, 100)
        # Feed enough mid data to avoid insufficient-data regime
        for _ in range(10):
            mgr.update_mid(0.50)
        signal = mgr.get_signal()
        # fill_mult=1.3, vol_mult=0.8 (low vol) → 1.04
        # But the adversity is detected:
        assert signal.fill_adversity_ratio == 1.0
        # Combined: 1.3 * 0.8 = 1.04 (low vol counteracts)
        assert signal.spread_multiplier > 1.0

    def test_high_vol_widens(self) -> None:
        mgr = AlphaOverlayManager(vol_high_bps=40, vol_high_mult=1.5)
        for i in range(30):
            price = 0.50 if i % 2 == 0 else 0.55
            mgr.update_mid(price)
        mgr.update_book(100, 100)
        signal = mgr.get_signal()
        assert signal.spread_multiplier >= 1.5
        assert signal.vol_regime == "high"

    def test_combined_multipliers_stack(self) -> None:
        """Fill adversity AND high vol should multiply together."""
        mgr = AlphaOverlayManager(
            fill_widen_threshold=0.6,
            fill_widen_multiplier=1.3,
            vol_high_bps=40,
            vol_high_mult=1.5,
        )
        # Add adverse fills
        for _ in range(10):
            mgr.record_fill("buy", 0.55, 0.50)
        # Add high vol (0.50 ↔ 0.55 → ~45 bps stdev > 40 threshold)
        for i in range(30):
            price = 0.50 if i % 2 == 0 else 0.55
            mgr.update_mid(price)
        mgr.update_book(100, 100)
        signal = mgr.get_signal()
        # 1.3 * 1.5 = 1.95
        assert signal.spread_multiplier >= 1.9

    def test_signal_fields_populated(self) -> None:
        mgr = AlphaOverlayManager()
        mgr.update_book(100, 100)
        mgr.update_mid(0.50)
        signal = mgr.get_signal()
        assert hasattr(signal, "imbalance_alpha_bps")
        assert hasattr(signal, "fill_adversity_ratio")
        assert hasattr(signal, "vol_regime")
        assert hasattr(signal, "realized_vol_bps")
        assert hasattr(signal, "complement_skew_bps")
        assert hasattr(signal, "depth_change_signal")

    def test_complement_arb_adds_skew(self) -> None:
        mgr = AlphaOverlayManager(complement_dead_zone_bps=30.0, complement_max_ticks=1)
        mgr.update_book(100, 100)
        for _ in range(10):
            mgr.update_mid(0.50)
        # Overpriced: YES=0.55 + NO=0.50 = 1.05 → 500 bps over
        mgr.update_complement(0.55, 0.50)
        signal = mgr.get_signal()
        assert signal.complement_skew_bps > 0
        # Should add positive skew (lean toward selling)
        assert signal.extra_skew_ticks >= 0

    def test_depth_change_tracked(self) -> None:
        mgr = AlphaOverlayManager(depth_min_delta=10.0)
        # First update sets baseline
        mgr.update_book(100, 100)
        # Big bid depth increase
        mgr.update_book(500, 100)
        for _ in range(10):
            mgr.update_mid(0.50)
        signal = mgr.get_signal()
        assert signal.depth_change_signal > 0  # Bid depth grew


# ── ComplementArbitrage ──────────────────────────────────────────────────


class TestComplementArbitrage:
    def test_balanced_complement_no_skew(self) -> None:
        ca = ComplementArbitrage(dead_zone_bps=50.0)
        result = ca.update(0.50, 0.50)  # sum = 1.0 exactly
        assert result == 0
        assert abs(ca.skew_bps) < 1.0

    def test_overpriced_positive_skew(self) -> None:
        ca = ComplementArbitrage(dead_zone_bps=30.0, max_skew_ticks=2)
        # sum = 1.05 → 500 bps over → well above dead zone
        result = ca.update(0.55, 0.50)
        assert result > 0, "Overpriced complement should produce positive skew"
        assert ca.skew_bps > 0

    def test_underpriced_negative_skew(self) -> None:
        ca = ComplementArbitrage(dead_zone_bps=30.0, max_skew_ticks=2)
        # sum = 0.94 → -600 bps → below fair
        result = ca.update(0.47, 0.47)
        assert result < 0, "Underpriced complement should produce negative skew"
        assert ca.skew_bps < 0

    def test_dead_zone_filters_noise(self) -> None:
        ca = ComplementArbitrage(dead_zone_bps=100.0)
        # sum = 1.005 → 50 bps, within dead zone
        result = ca.update(0.505, 0.50)
        assert result == 0

    def test_zero_mid_returns_zero(self) -> None:
        ca = ComplementArbitrage()
        result = ca.update(0.0, 0.50)
        assert result == 0
        assert ca.skew_bps == 0.0

    def test_max_ticks_respected(self) -> None:
        ca = ComplementArbitrage(dead_zone_bps=10.0, max_skew_ticks=1)
        # Extreme overpricing
        result = ca.update(0.70, 0.60)  # sum = 1.30 → 3000 bps
        assert abs(result) <= 1


# ── DepthRatioChange ─────────────────────────────────────────────────────


class TestDepthRatioChange:
    def test_first_update_returns_zero(self) -> None:
        drc = DepthRatioChange()
        assert drc.update(100, 100) == 0.0

    def test_bid_depth_increase_positive(self) -> None:
        drc = DepthRatioChange(min_delta=10.0)
        drc.update(100, 100)
        result = drc.update(500, 100)  # +400 bid, 0 ask
        assert result > 0, "Bid depth increase should be positive signal"
        assert abs(result) <= 1.0

    def test_ask_depth_increase_negative(self) -> None:
        drc = DepthRatioChange(min_delta=10.0)
        drc.update(100, 100)
        result = drc.update(100, 500)  # 0 bid, +400 ask
        assert result < 0, "Ask depth increase should be negative signal"

    def test_small_change_filtered(self) -> None:
        drc = DepthRatioChange(min_delta=100.0)
        drc.update(100, 100)
        result = drc.update(110, 100)  # Only +10 total change < 100 min
        assert result == 0.0

    def test_symmetric_change_zero(self) -> None:
        drc = DepthRatioChange(min_delta=10.0)
        drc.update(100, 100)
        # Both sides increase equally → (200 - 200) / (200 + 200) = 0
        result = drc.update(300, 300)
        assert result == 0.0

    def test_signal_property(self) -> None:
        drc = DepthRatioChange(min_delta=10.0)
        drc.update(100, 100)
        drc.update(500, 100)
        assert drc.signal > 0
